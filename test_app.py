import unittest
import json
import tempfile
import os
from unittest.mock import patch, MagicMock
from app import (
    app,
    init_db,
    get_cached_data,
    cache_data,
    is_allowed_domain,
    is_cacheable_path,
    should_cache_response,
    DB_PATH
)


class TestCacheProxy(unittest.TestCase):
    
    def setUp(self):
        """Set up test client and temporary database."""
        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        
        # Use temporary database for tests
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
        
        # Patch DB_PATH
        self.db_patcher = patch('app.DB_PATH', self.temp_db.name)
        self.db_patcher.start()
        
        init_db()
    
    def tearDown(self):
        """Clean up temporary database."""
        self.db_patcher.stop()
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)
    
    def test_is_allowed_domain(self):
        """Test domain validation."""
        self.assertTrue(is_allowed_domain("/openexchangerates.org/api/test"))
        self.assertTrue(is_allowed_domain("/api.twelvedata.com/eod"))
        self.assertFalse(is_allowed_domain("/evil.com/api"))
        self.assertFalse(is_allowed_domain("/random.org/data"))
    
    def test_is_cacheable_path(self):
        """Test cacheable path detection."""
        self.assertTrue(is_cacheable_path("/openexchangerates.org/api/historical/2024-01-01.json"))
        self.assertTrue(is_cacheable_path("/api.twelvedata.com/eod"))
        self.assertFalse(is_cacheable_path("/openexchangerates.org/api/latest"))
        self.assertFalse(is_cacheable_path("/api.twelvedata.com/other"))
    
    def test_should_cache_response_openexchangerates(self):
        """Test caching logic for openexchangerates."""
        path = "/openexchangerates.org/api/historical/2024-01-01.json"
        
        # Should cache 200 responses
        self.assertTrue(should_cache_response(path, 200, '{"rates": {}}'))
        
        # Should not cache non-200 responses
        self.assertFalse(should_cache_response(path, 404, '{"error": "not found"}'))
        self.assertFalse(should_cache_response(path, 500, '{"error": "server error"}'))
    
    def test_should_cache_response_twelvedata(self):
        """Test caching logic for twelvedata."""
        path = "/api.twelvedata.com/eod"
        
        # Should cache successful responses
        self.assertTrue(should_cache_response(path, 200, '{"close": "150.00"}'))
        
        # Should not cache rate limit errors
        self.assertFalse(should_cache_response(path, 200, '{"code": 429, "message": "rate limit"}'))
        
        # Should not cache 5xx errors
        self.assertFalse(should_cache_response(path, 200, '{"code": 500, "message": "server error"}'))
        
        # Should cache non-error responses
        self.assertTrue(should_cache_response(path, 200, '{"code": 200, "close": "150.00"}'))
    
    def test_cache_operations(self):
        """Test SQLite cache read/write operations."""
        request_hash = "test_hash_123"
        test_data = '{"test": "data"}'
        
        # Initially should return None
        self.assertIsNone(get_cached_data(request_hash))
        
        # Cache data
        cache_data(request_hash, test_data)
        
        # Should retrieve cached data
        cached = get_cached_data(request_hash)
        self.assertEqual(cached, test_data)
        
        # Update cached data
        new_data = '{"updated": "data"}'
        cache_data(request_hash, new_data)
        
        # Should retrieve updated data
        cached = get_cached_data(request_hash)
        self.assertEqual(cached, new_data)
    
    def test_forbidden_domain(self):
        """Test that forbidden domains return 403."""
        response = self.client.get('/evil.com/api/data')
        self.assertEqual(response.status_code, 403)
        data = json.loads(response.data)
        self.assertIn('error', data)
        self.assertEqual(data['error'], 'Domain not allowed')
    
    @patch('app.urllib.request.urlopen')
    def test_cache_hit(self, mock_urlopen):
        """Test cache hit scenario."""
        # Pre-populate cache
        path = "/openexchangerates.org/api/historical/2024-01-01.json"
        query = "app_id=test&base=USD"
        full_path = f"{path}?{query}"
        
        import hashlib
        request_hash = hashlib.sha256(full_path.encode()).hexdigest()
        cached_response = '{"rates": {"AUD": 1.5}}'
        cache_data(request_hash, cached_response)
        
        # Make request
        response = self.client.get(f'{path}?{query}')
        
        # Should return cached data without calling upstream
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-Cache'), 'HIT')
        self.assertEqual(response.data.decode(), cached_response)
        mock_urlopen.assert_not_called()
    
    @patch('app.urllib.request.urlopen')
    def test_cache_miss_and_store(self, mock_urlopen):
        """Test cache miss and subsequent storage."""
        # Mock upstream response
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"rates": {"AUD": 1.5}}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        
        path = "/openexchangerates.org/api/historical/2024-01-01.json"
        query = "app_id=test&base=USD"
        
        # Make request
        response = self.client.get(f'{path}?{query}')
        
        # Should call upstream and cache response
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-Cache'), 'MISS')
        mock_urlopen.assert_called_once()
        
        # Verify data was cached
        full_path = f"{path}?{query}"
        import hashlib
        request_hash = hashlib.sha256(full_path.encode()).hexdigest()
        cached = get_cached_data(request_hash)
        self.assertEqual(cached, '{"rates": {"AUD": 1.5}}')
    
    @patch('app.urllib.request.urlopen')
    def test_non_cacheable_path(self, mock_urlopen):
        """Test that non-cacheable paths don't get cached."""
        # Mock upstream response
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"rates": {"AUD": 1.5}}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response
        
        path = "/openexchangerates.org/api/latest.json"
        query = "app_id=test"
        
        # Make request
        response = self.client.get(f'{path}?{query}')
        
        # Should call upstream but not cache
        self.assertEqual(response.status_code, 200)
        mock_urlopen.assert_called_once()
        
        # Verify data was NOT cached
        full_path = f"{path}?{query}"
        import hashlib
        request_hash = hashlib.sha256(full_path.encode()).hexdigest()
        cached = get_cached_data(request_hash)
        self.assertIsNone(cached)


if __name__ == '__main__':
    unittest.main()
