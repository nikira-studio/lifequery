"""Error beautifier utility for LifeQuery - converts technical errors into human-friendly messages."""

def beautify_error(e: Exception) -> str:
    """Convert technical exceptions into user-friendly messages.
    
    Specifically handles OpenAI and common API error patterns.
    """
    error_msg = str(e)
    exc_type = type(e).__name__
    
    # OpenAI / API specific handling
    if "AuthenticationError" in exc_type:
        return "Authentication Failed: Please check your API Key in the Settings tab. The provider rejected the current key."
    
    if "RateLimitError" in exc_type:
        return "Rate Limit Exceeded: The AI provider is temporarily busy. Please wait a few seconds and try again."
    
    if "APIConnectionError" in exc_type:
        return "Connection Error: Could not reach the provider. Please check your internet connection and the Provider URL in Settings."
    
    if "NotFoundError" in exc_type:
        return "Model Not Found: The selected model name appears to be incorrect for this provider. Check your model selection in Settings."

    # Pattern based handling for other providers
    lower_msg = error_msg.lower()
    if "login fail" in lower_msg or "authorized_error" in lower_msg:
        return "Provider Authentication Error: Your API key seems to be invalid or expired. Please update it in Settings."
    
    if "404" in error_msg and "not found" in lower_msg:
        return "Resource Not Found (404): The API URL might be incorrect or the model is unavailable. Check Settings."

    if "connection" in lower_msg and "refused" in lower_msg:
        return "Connection Refused: The local AI service (Ollama) might not be running or the URL is incorrect."

    # Fallback to the original message if it's already somewhat readable
    if len(error_msg) > 0 and len(error_msg) < 200:
        return error_msg
        
    return f"An unexpected error occurred: {exc_type}"
