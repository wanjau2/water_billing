import africastalking

africastalking.initialize(
    username="sandbox",
    api_key="atsk_6eea60ddee332a4db82af47f0ce7d472d08ac5cdf0c13476bf69e50639bad410d031d609"
    
)
sms = africastalking.SMS
sender = 'qwertyuiop'


def send_message(recipient, message):
    try:
        response = sms.send(message, [recipient], sender)
        return response
    except Exception as e:
        print(f"Error sending message: {e}")
        return False