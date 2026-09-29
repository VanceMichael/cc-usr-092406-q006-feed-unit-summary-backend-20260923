"""袋/克/公斤换算的规定精度测试。"""

import unittest

from backend.app.services.units import (
    to_kilograms, round_kg, normalize_unit,
    InvalidUnitError, SpecRequiredError,
)


class UnitConversionTest(unittest.TestCase):
    def test_bag_uses_registered_net_weight(self):
        self.assertEqual(to_kilograms(2, "bag", 40), 80.0)
        self.assertEqual(to_kilograms(0.5, "bag", 20), 10.0)
        # 分数袋同样按规格换算，结果保留 3 位小数
        self.assertEqual(to_kilograms(3, "bag", 20.04), 60.12)

    def test_gram_to_kg(self):
        self.assertEqual(to_kilograms(1500, "g"), 1.5)
        self.assertEqual(to_kilograms(1, "g"), 0.001)
        self.assertEqual(to_kilograms(0.4, "g"), 0.0)  # 不足 0.5 克按 3 位精度舍去
        self.assertEqual(to_kilograms(0.6, "g"), 0.001)

    def test_kg_round_half_up_to_3_places(self):
        self.assertEqual(to_kilograms(1.2345, "kg"), 1.235)
        self.assertEqual(to_kilograms(2.71828, "kg"), 2.718)

    def test_bag_without_spec_is_refused_not_guessed(self):
        with self.assertRaises(SpecRequiredError):
            to_kilograms(1, "bag")

    def test_invalid_unit_and_negative_quantity(self):
        with self.assertRaises(InvalidUnitError):
            to_kilograms(1, "石")  # 系统不存在的单位，绝不猜测
        with self.assertRaises(ValueError):
            to_kilograms(-1, "kg")

    def test_normalize_unit_aliases(self):
        self.assertEqual(normalize_unit("袋"), "bag")
        self.assertEqual(normalize_unit("BAGS"), "bag")
        self.assertEqual(normalize_unit("克"), "g")
        self.assertEqual(normalize_unit("公斤"), "kg")
        self.assertIsNone(normalize_unit("吨"))

    def test_round_kg_helper(self):
        self.assertEqual(round_kg(10.1234), 10.123)
        self.assertEqual(round_kg(None), 0.0)


if __name__ == "__main__":
    unittest.main()
