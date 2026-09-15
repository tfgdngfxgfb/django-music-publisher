from django.test import SimpleTestCase

from music_publisher.societies import SOCIETY_DICT


class SocietyEncodingTest(SimpleTestCase):
    def test_unicode_society_name_is_decoded_as_utf8(self):
        name = SOCIETY_DICT["152"]

        self.assertIn("Bildupphovsrätt", name)
        self.assertNotIn("BildupphovsrÃ¤tt", name)
