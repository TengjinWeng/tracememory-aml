"""Counterexamples for image intent, wrong-image promotion and source isolation."""
import unittest

from system_service.modality import wants_image_evidence, prefer_image_evidence


class ModalityTests(unittest.TestCase):
    def test_explicit_english_and_chinese_image_requests(self):
        for query in ('Show the original photograph of the bicycle.', '哪张照片有蓝色的背包？', '找出会议通知的原图。'):
            self.assertTrue(wants_image_evidence(query))

    def test_text_requests_and_negated_images_do_not_trigger_preference(self):
        for query in ('Where was the bicycle parked?', 'What does photograph mean?',
                      'Find the text-only record about the photograph.', 'Find records without images.',
                      '找一下猫的介绍，不要图片。', '只要文字描述的照片内容。'):
            self.assertFalse(wants_image_evidence(query), query)
        self.assertFalse(wants_image_evidence('Find the text-only catalogue description.', True))

    def test_comparable_image_overtakes_text_without_promoting_unrelated_picture(self):
        scores = {'text': .17, 'image': .16, 'unrelated-picture': .12}
        docs = {key: {'pixels': [] if key == 'text' else ['pixels-' + key]} for key in scores}
        prefer_image_evidence(scores, {'text': .69, 'image': .65, 'unrelated-picture': .2}, docs)
        self.assertGreater(scores['image'], scores['text'])
        self.assertEqual(scores['unrelated-picture'], .12)
        self.assertEqual(scores['text'], .17)

    def test_strong_text_identifier_is_not_overridden_by_a_weak_image(self):
        scores = {'text': .31, 'weak-picture': .12}
        docs = {'text': {'pixels': []}, 'weak-picture': {'pixels': ['pixels']}}
        prefer_image_evidence(scores, {'text': .8, 'weak-picture': .4}, docs)
        self.assertEqual(scores, {'text': .31, 'weak-picture': .12})

    def test_no_semantic_evidence_does_not_create_an_image_match(self):
        scores = {'image': .02}
        prefer_image_evidence(scores, {}, {'image': {'pixels': ['pixels']}})
        self.assertEqual(scores['image'], .02)


if __name__ == '__main__':
    unittest.main()
