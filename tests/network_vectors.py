"""Synthetic network vectors for the locality gate.

These are *inputs*, not addresses of anything. They have to stay inside RFC 1918:
the assertions they feed prove the gate refuses private callers, so a vector from
RFC 5737's documentation ranges would exercise a different branch and the negative
assertion would stop meaning anything.

Which is why there are exactly three, and why they are these three. Push-time
secret scanning treats any other RFC 1918 quad as a private address worth stopping
a push over, and exempts only the first host of each block - every network has
something at ``.1``, so the address says nothing about whose. Do not write an
RFC 1918 literal that is not from here; ``tests/test_architecture_guardrails.py``
fails the build on one.

Loopback, CGNAT (``100.64.0.0/10``) and public addresses are not private and were
never a finding, so they stay written out where they are used.
"""

LAN_IPV4 = "192.168.0.1"
PRIVATE_10_IPV4 = "10.0.0.1"
PRIVATE_172_IPV4 = "172.16.0.1"
