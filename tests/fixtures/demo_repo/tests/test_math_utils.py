import unittest

from app.math_utils import inclusive_sum


class TestMathUtils(unittest.TestCase):
    def test_inclusive_sum(self) -> None:
        self.assertEqual(inclusive_sum(3), 6)


if __name__ == "__main__":
    unittest.main()

