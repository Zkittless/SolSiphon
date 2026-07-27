"""
Solana address validation.

This module only checks *format* -- base58, correct decoded length. It does
NOT confirm the address is a real, initialized on-chain account. That's an
RPC call (getAccountInfo) and belongs in Phase 2 alongside the rest of the
chain-interaction code, not here.

Treat a format-valid address as "not obviously wrong," not as "confirmed
correct." The test-transaction + user-confirmation flow from the design doc
is still the real safeguard against a right-format-wrong-wallet mistake.
"""

import base58

SOLANA_ADDRESS_BYTE_LENGTH = 32


def is_valid_solana_address_format(address: str) -> bool:
    """Return True if `address` decodes as a plausible Solana public key."""
    if not address or not isinstance(address, str):
        return False

    address = address.strip()

    # Solana addresses are base58-encoded, no 0/O/I/l characters, roughly 32-44 chars
    if not (32 <= len(address) <= 44):
        return False

    try:
        decoded = base58.b58decode(address)
    except ValueError:
        return False

    return len(decoded) == SOLANA_ADDRESS_BYTE_LENGTH
