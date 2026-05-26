import unittest

from app import greeting


class GreetingTest(unittest.TestCase):
    def test_formats_name(self):
        self.assertEqual(greeting("Continuum"), "Hello, Continuum!")


if __name__ == "__main__":
    unittest.main()
