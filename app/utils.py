def format_reply(message, links=None):
    reply = message
    if links:
        reply += "\n\nOfficial Links:"
        for l in links:
            reply += f"\n🔗 {l}"
    return reply
