import os
import unittest
from unittest.mock import patch, MagicMock
import json
import requests
from dotenv import load_dotenv

# Import the function to test from home.py
from home import send_message, TALKSASA_API_KEY, TALKSASA_SENDER_ID, format_phone_number

class TestTalkSasaSMS(unittest.TestCase):
    """Test cases for TalkSasa SMS integration"""
    
    def setUp(self):
        """Set up test environment"""
        # Load environment variables
        load_dotenv()
        self.api_key = TALKSASA_API_KEY
        self.sender_id = TALKSASA_SENDER_ID
        self.test_phone = "+254748507336"  # Test phone number
        self.test_message = "This is a test message"
        
    @patch('requests.post')
    def test_send_message_success(self, mock_post):
        """Test successful message sending"""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "message": "Message sent successfully",
            "data": {
                "message_id": "12345",
                "phone": self.test_phone,
                "cost": "1.0"
            }
        }
        mock_post.return_value = mock_response
        
        # Call the function
        result = send_message(self.test_phone, self.test_message)
        
        # Assertions
        self.assertEqual(result["status"], "success")
        mock_post.assert_called_once()
        
        # Verify the payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload["api_key"], self.api_key)
        self.assertEqual(payload["sender_id"], self.sender_id)
        self.assertEqual(payload["phone"], self.test_phone)
        self.assertEqual(payload["message"], self.test_message)
    
    @patch('requests.post')
    def test_send_message_api_failure(self, mock_post):
        """Test API failure response"""
        # Mock failure response
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "status": "error",
            "message": "Invalid phone number"
        }
        mock_post.return_value = mock_response
        
        # Call the function
        result = send_message(self.test_phone, self.test_message)
        
        # Assertions
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Invalid phone number")
    
    @patch('requests.post')
    def test_send_message_exception(self, mock_post):
        """Test exception handling"""
        # Mock exception
        mock_post.side_effect = Exception("Connection error")
        
        # Call the function
        result = send_message(self.test_phone, self.test_message)
        
        # Assertions
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Connection error")
    
    @patch('requests.post')
    def test_send_message_with_custom_sender(self, mock_post):
        """Test sending with custom sender ID"""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success"}
        mock_post.return_value = mock_response
        
        custom_sender = "CUSTOM"
        
        # Call the function with custom sender
        result = send_message(self.test_phone, self.test_message, sender=custom_sender)
        
        # Verify the sender in payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload["sender_id"], custom_sender)
    
    def test_no_api_key_configured(self):
        """Test behavior when API key is not configured"""
        # Temporarily set API key to None
        with patch('home.TALKSASA_API_KEY', None):
            result = send_message(self.test_phone, self.test_message)
            self.assertIn("error", result)
            self.assertEqual(result["error"], "SMS service not configured")
    
    def test_phone_number_formatting(self):
        """Test phone number formatting function"""
        test_cases = [
            ("0712345678", "+254712345678"),
            ("254712345678", "+254712345678"),
            ("+254712345678", "+254712345678"),
            ("712345678", "+254712345678")
        ]
        
        for input_number, expected_output in test_cases:
            self.assertEqual(format_phone_number(input_number), expected_output)
    
    def test_invalid_phone_number(self):
        """Test invalid phone number handling"""
        invalid_numbers = [
            "abcdefg",
            "123+456",
            "+254abc123"
        ]
        
        for number in invalid_numbers:
            with self.assertRaises(ValueError):
                format_phone_number(number)
    
    @patch('requests.post')
    def test_retry_mechanism(self, mock_post):
        """Test the retry mechanism"""
        # First two calls fail, third succeeds
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 500
        mock_response_fail.json.return_value = {"status": "error", "message": "Server error"}
        
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {"status": "success"}
        
        mock_post.side_effect = [mock_response_fail, mock_response_fail, mock_response_success]
        
        # Call with 3 retries
        result = send_message(self.test_phone, self.test_message, retries=3)
        
        # Should succeed on the third try
        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_post.call_count, 3)

if __name__ == '__main__':
    unittest.main()