import re

BANNED_WORDS = [
    "nigger", "nigga", "faggot", "retard", "chink", "spic", "kike",
    # add more as needed
]

def filter_response(text):
    """Clean stage directions, asterisks, and banned words for clean TTS."""
    if not text:
        return ""
        
    filtered = text
    
    # 1. Remove stage directions / roleplay actions like *sighs*, *giggles*, [laughs], (screams)
    filtered = re.sub(r'\*.*?\*', '', filtered)
    filtered = re.sub(r'\[.*?\]', '', filtered)
    filtered = re.sub(r'\(.*?\)', '', filtered)
    
    # 2. Remove stray asterisks, quotes, or markdown artifacts
    filtered = filtered.replace('*', '').replace('"', '').replace('`', '')
    
    # 3. Clean up extra whitespace
    filtered = re.sub(r'\s+', ' ', filtered).strip()
    
    # 4. Filter banned slurs
    for word in BANNED_WORDS:
        pattern = re.compile(rf'\b{re.escape(word)}\b', re.IGNORECASE)
        filtered = pattern.sub("", filtered)
        
    return filtered.strip()