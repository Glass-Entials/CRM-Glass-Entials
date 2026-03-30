import re


def validate_gst(gstin):
    """
    Validate GSTIN format (15 characters):
    - 2 digits: State code (01-37)
    - 5 chars: PAN holder's name (A-Z)
    - 4 digits: PAN numeric part
    - 1 char: PAN entity type (A-Z)
    - 1 char: Entity number (1-9, A-Z)
    - 1 char: Always 'Z'
    - 1 char: Checksum (0-9, A-Z)
    Example: 22AAAAA0000A1Z5
    """
    if not gstin or not isinstance(gstin, str):
        return False

    gstin = gstin.strip().upper()
    pattern = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")
    return bool(pattern.match(gstin))
