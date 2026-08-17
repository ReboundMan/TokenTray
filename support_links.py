"""Outbound links and support wording the UI offers, in one place.

Mirrors WordMD's ``SupportLinks.cs`` (its spec 10024/tip-jar model): the app
never holds a payment secret, it only opens public URLs in the browser.

``TIP_URL`` points at a reboundman.com redirect, NOT at the payment provider
directly. A URL compiled into a signed installer is effectively permanent
once shipped (fleet finding WordMD already paid for), so the Caddyfile's
``/tokentray-tip`` line owns the real destination: Buy Me a Coffee today, a
Stripe Payment Link with ``client_reference_id=tokentray-app`` once a
TokenTray Stripe product exists. Repointing the destination needs no app
release.

FLIP CHECKLIST, for when the Stripe product is created. It is deliberately
NOT a one-line change; the wording is coupled to the destination and these
move in lockstep (fleet finding, Sage):

1. reboundman.com Caddyfile: repoint ``/tokentray-tip`` to the Stripe link.
2. ``TIP_LABEL`` and ``TIP_DISCLAIMER`` below: match the Stripe product's
   actual checkout wording and default amount (WordMD precedent: the button
   must not say one thing and land on checkout saying another).
3. ``tests/test_about_dialog.py``: the label/destination assertions.
4. reboundman.com ``tokentray.html``: add a thanks section (direct Stripe
   link with ``client_reference_id=tokentray-web``, like wordmd.html's).
5. Ship an app release so installed copies pick up the new wording (the
   destination itself already flipped in step 1 for every installed copy).

SKU note for that setup, recorded here because the fleet flagged it (Sage,
High): unlike WordMD's pure thank-you tip, this payment UNLOCKS a gated
feature (post-trial Advanced history recording). Do not clone the
``wordmd_tip_once`` SKU or its "grant strategy: none" treatment; the
TokenTray product is a sale with something granted, which matters for
Stripe Tax treatment, the refunds page, and PAYMENTS-JJ.md's catalog.
"""
from __future__ import annotations

GITHUB = "https://github.com/ReboundMan/TokenTray"
PRODUCT_PAGE = "https://reboundman.com/tokentray.html"

# Stable app-side URL; the Caddyfile owns the real destination (see above).
TIP_URL = "https://reboundman.com/tokentray-tip"

# Wording matches the CURRENT destination (Buy Me a Coffee). Change only via
# the flip checklist above, never independently of the destination.
TIP_LABEL = "Buy me a coffee"
TIP_DISCLAIMER = (
    "Opens buymeacoffee.com in your browser. "
    "TokenTray never sees your payment details."
)
