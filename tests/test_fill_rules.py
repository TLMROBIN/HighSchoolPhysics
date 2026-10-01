import unittest
from highschoolphysics.outcomes import decide
from highschoolphysics.fill_rules import teacher_rule


class FillRulesTests(unittest.TestCase):
    def rule(self, **changes):
        return dict(type='fill', answer='10', match='numeric_quantity', unit='m/s',
                    allow_unit_conversion=True, unit_required=True, **changes)

    def test_conversion_sign_dimension_and_explicit_requirement(self):
        rule = self.rule()
        self.assertEqual(decide(rule, '36 km/h')['outcome'], 'correct')
        self.assertEqual(decide(rule, '-10 m/s')['outcome'], 'wrong')
        self.assertEqual(decide(rule, '10 m')['reason_code'], 'unit_mismatch')
        self.assertEqual(decide(rule, '10')['reason_code'], 'unit_required')
        self.assertEqual(decide(dict(rule, allow_unit_conversion=False), '36 km/h')['reason_code'], 'unit_conversion_disabled')
        self.assertEqual(decide(dict(rule, unit_required=False), '10')['outcome'], 'correct')

    def test_fraction_unicode_and_exact_boundary(self):
        rule = dict(self.rule(), answer='1/2', unit='m', absolute_tolerance='0.01')
        for answer in ['５０ cm', '0.5 m', '1/2 m', '5e-1 m', '0.51 m']:
            self.assertEqual(decide(rule, answer)['outcome'], 'correct', answer)
        self.assertEqual(decide(rule, '0.51000001 m')['outcome'], 'wrong')
        self.assertEqual(decide(dict(rule, answer='9.8', unit='m/s^2'), '9.8 m/s²')['outcome'], 'correct')

    def test_relative_tolerance_zero_and_config_precision(self):
        rule = dict(self.rule(), relative_tolerance='0.01')
        self.assertEqual(decide(rule, '10.1 m/s')['outcome'], 'correct')
        self.assertEqual(decide(rule, '10.10001 m/s')['outcome'], 'wrong')
        self.assertEqual(decide(dict(rule, answer='0'), '0.01 m/s')['outcome'], 'wrong')
        rule = dict(rule, significant_figures=3)
        self.assertEqual(decide(rule, '10.0 m/s')['outcome'], 'correct')
        for answer in ['10 m/s', '10.00 m/s', '20/2 m/s']:
            self.assertEqual(decide(rule, answer)['reason_code'], 'precision_review')
        self.assertEqual(decide(rule, '1.00e1 m/s')['outcome'], 'correct')

    def test_unsupported_and_invalid_inputs_never_guess(self):
        rule = self.rule()
        for answer in ['NaN m/s', 'inf m/s', '1/0 m/s', '1e999 m/s', '__import__("os")', '10 MA', '10 m s^-1', '10 →m/s']:
            self.assertEqual(decide(rule, answer)['outcome'], 'pending', answer)
        self.assertEqual(decide(dict(rule, answer='1/0'), '10 m/s')['reason_code'], 'answer_rule')
        for change in [dict(absolute_tolerance='-1'), dict(relative_tolerance='NaN'), dict(significant_figures=0), dict(unit='unknown')]:
            self.assertEqual(decide(dict(rule, **change), '10 m/s')['outcome'], 'pending')
        self.assertEqual(decide(rule, '')['outcome'], 'blank')
        self.assertEqual(decide(rule, '36 km/h', verified=False)['outcome'], 'pending')

    def test_old_rules_keep_conservative_semantics(self):
        self.assertEqual(decide({'type':'fill','answer':'10 m/s'},'36 km/h')['outcome'],'pending')
        self.assertEqual(decide({'type':'fill','answer':'1/2','match':'numeric_tolerance'},'0.5')['outcome'],'pending')
        self.assertEqual(decide({'type':'fill','answer':['匀速','匀速直线运动']},'匀速')['outcome'],'correct')

    def test_teacher_configuration_validated_before_use(self):
        rule=teacher_rule(dict(answer='10',fill_match='numeric_quantity',unit='m/s',unit_required='on',allow_unit_conversion='on'))
        self.assertTrue(rule['unit_required'])
        self.assertEqual(teacher_rule(dict(answer='匀速\n匀速直线运动',fill_match='aliases'))['answer'],['匀速','匀速直线运动'])
        for fields in [dict(answer='10',unit='m',absolute_tolerance='-1'),dict(answer='10 m',unit='s'),dict(answer='10',significant_figures='2.5')]:
            with self.assertRaises(ValueError):teacher_rule(dict(fill_match='numeric_quantity',**fields))
