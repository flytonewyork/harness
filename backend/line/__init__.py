"""The doctor's own line (spec 005): a live, multi-doctor WhatsApp care line on one shared number.

Patients scan their doctor's QR code, join, and get the doctor's plan written from a voice note. Doctors work from
their own WhatsApp (and a web page). The safety core is spec 004's (`backend.careline.safety`): fixed texts, red flags
first, every outbound message checked before it is sent, and only the doctor approves clinical words.

Real patient data lives only in the live deployment's store (Firestore, Google Cloud `asia-east2`). Tests, sim and
every Claude session use fakes and synthetic people.
"""
