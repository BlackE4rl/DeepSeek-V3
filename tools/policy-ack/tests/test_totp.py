import unittest

from policyack import totp

# RFC 6238, Anhang B: Secret "12345678901234567890" in Base32.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class TotpTest(unittest.TestCase):
    def test_rfc6238_vectors(self):
        # (Zeitstempel, erwarteter SHA1-Code) aus der Referenztabelle des RFC.
        for timestamp, expected in ((59, "287082"), (1111111109, "081804"), (1234567890, "005924")):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(totp.code_at(RFC_SECRET, timestamp // totp.PERIOD), expected)

    def test_verify_accepts_current_code(self):
        secret = totp.new_secret()
        now = 1_700_000_000
        code = totp.code_at(secret, totp.counter_for(now))
        self.assertIsNotNone(totp.verify(secret, code, timestamp=now))

    def test_verify_rejects_replay(self):
        secret = totp.new_secret()
        now = 1_700_000_000
        counter = totp.counter_for(now)
        code = totp.code_at(secret, counter)
        accepted = totp.verify(secret, code, timestamp=now)
        self.assertEqual(accepted, counter)
        # Derselbe Code darf nach dem Merken des Zählers nicht erneut greifen.
        self.assertIsNone(totp.verify(secret, code, last_counter=accepted, timestamp=now))

    def test_verify_tolerates_one_step_drift(self):
        secret = totp.new_secret()
        now = 1_700_000_000
        previous = totp.code_at(secret, totp.counter_for(now) - 1)
        self.assertIsNotNone(totp.verify(secret, previous, timestamp=now))

    def test_verify_rejects_garbage(self):
        secret = totp.new_secret()
        for value in ("", "12345", "abcdef", "1234567"):
            with self.subTest(value=value):
                self.assertIsNone(totp.verify(secret, value))

    def test_provisioning_uri_contains_parameters(self):
        uri = totp.provisioning_uri("ABCDEFGH", "erika@example.org", "Stadtwerke")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn("secret=ABCDEFGH", uri)
        self.assertIn("issuer=Stadtwerke", uri)
        self.assertIn("digits=6", uri)


if __name__ == "__main__":
    unittest.main()
