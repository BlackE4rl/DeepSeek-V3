"""Policy-Ack: Verteilung von Informationen und Anweisungen in drei Stufen.

Stufe 1  nur Information (Zustellnachweis, keine Rückmeldung)
Stufe 2  Bestätigung über einen persönlichen Link in der E-Mail
Stufe 3  wie Stufe 2, zusätzlich abgesichert durch MFA (TOTP)

Das Paket kommt ohne Fremdbibliotheken aus (Standardbibliothek Python 3.11+).
"""

__version__ = "1.0.0"
