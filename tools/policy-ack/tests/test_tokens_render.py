import unittest

from policyack import render, tokens


class TokenTest(unittest.TestCase):
    def test_tokens_are_unique_and_long(self):
        values = {tokens.new_token() for _ in range(200)}
        self.assertEqual(len(values), 200)
        self.assertTrue(all(len(value) >= 40 for value in values))

    def test_hash_depends_on_pepper(self):
        token = tokens.new_token()
        self.assertNotEqual(tokens.hash_token(token, "a"), tokens.hash_token(token, "b"))
        self.assertTrue(tokens.matches(token, tokens.hash_token(token, "a"), "a"))
        self.assertFalse(tokens.matches(token, tokens.hash_token(token, "a"), "b"))

    def test_form_check(self):
        self.assertTrue(tokens.looks_like_token(tokens.new_token()))
        for value in ("", "kurz", "hat leerzeichen drin und ist lang genug", "a" * 200):
            with self.subTest(value=value):
                self.assertFalse(tokens.looks_like_token(value))


class RenderTest(unittest.TestCase):
    def test_html_in_body_is_escaped(self):
        out = render.markdown_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_headings_lists_and_tables(self):
        out = render.markdown_to_html(
            "# Titel\n\n- eins\n- zwei\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        )
        self.assertIn("<h2>Titel</h2>", out)  # h1 bleibt dem Seitentitel vorbehalten
        self.assertIn("<li>eins</li>", out)
        self.assertIn("<th>A</th>", out)
        self.assertIn("<td>2</td>", out)

    def test_inline_marks(self):
        out = render.markdown_to_html("Ein **fettes** und *kursives* Wort mit `Code`.")
        self.assertIn("<strong>fettes</strong>", out)
        self.assertIn("<em>kursives</em>", out)
        self.assertIn("<code>Code</code>", out)

    def test_only_safe_link_schemes(self):
        out = render.markdown_to_html("[gut](https://example.org) [boese](javascript:alert(1))")
        self.assertIn('href="https://example.org"', out)
        self.assertNotIn("javascript:", out)

    def test_page_has_no_external_resources(self):
        page = render.page("Titel", "<p>Text</p>", organisation="Org").decode("utf-8")
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertIn("<title>Titel</title>", page)


if __name__ == "__main__":
    unittest.main()
