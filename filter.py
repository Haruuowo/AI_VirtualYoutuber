import re

BANNED_WORDS = [
    "nigger", "nigga", "faggot", "retard", "chink", "spic", "kike",
    # add more as needed
]

def filter_response(text):
    """Clean speaker prefixes, stage directions, asterisks, and banned words for clean TTS."""
    if not text:
        return ""
        
    filtered = text.strip()
    
    # 1. Strip leading speaker labels like "Luna:", "Luna :", "Assistant:", "Bot:", "Luna -"
    filtered = re.sub(r'^(luna|assistant|bot)\s*[:\-]\s*', '', filtered, flags=re.IGNORECASE)
    
    # Repeat in case there was "Luna: Luna:"
    filtered = re.sub(r'^(luna|assistant|bot)\s*[:\-]\s*', '', filtered, flags=re.IGNORECASE)

    # 2. Remove stage directions / roleplay actions like *sighs*, *giggles*, [laughs], (screams)
    filtered = re.sub(r'\*.*?\*', '', filtered)
    filtered = re.sub(r'\[.*?\]', '', filtered)
    filtered = re.sub(r'\(.*?\)', '', filtered)
    
    # 3. Remove stray asterisks, quotes, or markdown artifacts and normalize quotes
    filtered = filtered.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    filtered = filtered.replace('*', '').replace('"', '').replace('`', '')
    
    # 4. Clean up extra whitespace
    filtered = re.sub(r'\s+', ' ', filtered).strip()
    
    # 5. Filter banned slurs
    for word in BANNED_WORDS:
        pattern = re.compile(rf'\b{re.escape(word)}\b', re.IGNORECASE)
        filtered = pattern.sub("", filtered)
        
    return filtered.strip()