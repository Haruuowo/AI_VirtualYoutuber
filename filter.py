import re

BANNED_WORDS = [
    "nigger", "nigga", "faggot", "retard", "chink", "spic", "kike",
    "cunt", "fuck", "shit", "bitch", "ass", "bastard", "whore", "slut",
    # add more as needed
]

def filter_response(text):
    """Replace banned words with [censored]."""
    filtered = text
    for word in BANNED_WORDS:
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        filtered = pattern.sub("[censored]", filtered)
    return filtered