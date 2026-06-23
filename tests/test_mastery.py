import unittest

from highschoolphysics.mastery import classify_mastery


class MasteryThresholdTests(unittest.TestCase):
    def test_classify_mastery_boundaries(self):
        cases = [
            (0, None, "未练习"),
            (1, 0.29, "未掌握"),
            (1, 0.30, "有困难"),
            (1, 0.59, "有困难"),
            (1, 0.60, "不熟练"),
            (1, 0.79, "不熟练"),
            (1, 0.80, "已掌握"),
        ]
        for attempts, rate, expected in cases:
            with self.subTest(attempts=attempts, rate=rate):
                self.assertEqual(classify_mastery(attempts, rate), expected)


if __name__ == "__main__":
    unittest.main()
