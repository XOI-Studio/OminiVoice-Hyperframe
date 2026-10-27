import re

_SENT = re.compile(r"(?<=[.!?…])\s+|\n+")


def split_text(text: str, limit: int) -> list[str]:
    """Split on sentence ends, packing sentences into chunks of at most `limit` chars."""
    chunks, cur = [], ""
    for sent in (s.strip() for s in _SENT.split(text)):
        if not sent:
            continue
        while len(sent) > limit:  # one very long sentence: break on the last space before the limit
            cut = sent.rfind(" ", 0, limit) or limit
            if cut <= 0:
                cut = limit
            chunks.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if cur and len(cur) + 1 + len(sent) > limit:
            chunks.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        chunks.append(cur)
    return chunks
