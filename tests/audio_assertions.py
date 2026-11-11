"""Compare MPEG audio payload independently of intentionally changed ID3 text tags."""
def mpeg_payload(data):
    if data.startswith(b'ID3'):
        size = 0
        for value in data[6:10]: size = (size << 7) | value
        data = data[10 + size:]
    if len(data) >= 128 and data[-128:-125] == b'TAG': data = data[:-128]
    return data
