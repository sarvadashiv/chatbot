def extract_status(notices, keyword):
    for n in notices:
        if keyword in n["text"].lower():
            return True, n
    return False, None