import unittest

from pi_vision.shared.spatial_detection import SpatialDetector, search_regions, translate_detections, merge_detections
from pi_vision.studio.training_data import native_crops


def plane(x, y):
    return dict(label='plane', confidence=.9, box=[x-6, y-4, x+6, y+4])


class SpatialDetectionTests(unittest.TestCase):
    def test_tile_duplicates_merge_per_class_without_losing_distinct_planes(self):
        first=plane(100,100)
        duplicate={**first,'confidence':.7,'box':[95,96,107,104]}
        other=plane(200,200)
        target={**first,'label':'target'}
        self.assertEqual(merge_detections([duplicate,other,target,first]),[other,target,first])

    def test_search_covers_full_1080p_without_resizing_and_with_overlap(self):
        regions = search_regions(1920, 1080, 640)
        self.assertEqual(len(regions), 8)
        for y in range(1080):
            spans = sorted((x, x+w) for x, top, w, h in regions if top <= y < top+h)
            end = 0
            for left, right in spans:
                self.assertLessEqual(left, end)
                end = max(end, right)
            self.assertEqual(end, 1920)
        self.assertTrue(all(w == h == 640 for _, _, w, h in regions))

    def test_tracking_uses_original_pixel_geometry_at_image_edges(self):
        detector = SpatialDetector()
        detector.region(1920, 1080, 1)
        detector.observe([plane(1910, 1070)], 1)
        detector.observe([plane(1910, 1070)], 1.005)
        detector.observe([plane(1910, 1070)], 1.01)
        region, mode = detector.region(1920, 1080, 1.02)
        self.assertEqual(region, (1280, 440, 640, 640))
        self.assertEqual(mode, 'tracking')
        self.assertEqual(translate_detections([plane(630, 630)], region), [plane(1910, 1070)])

    def test_misses_ambiguity_age_and_geometry_resume_search(self):
        for cause in ('misses', 'ambiguity', 'age', 'geometry'):
            with self.subTest(cause=cause):
                detector = SpatialDetector()
                detector.region(1920, 1080, 1)
                detector.observe([plane(900, 500)], 1)
                if cause == 'misses':
                    detector.observe([], 1.02)
                    detector.observe([], 1.04)
                if cause == 'ambiguity':
                    detector.observe([plane(900,500), plane(950,500)], 1.02)
                region, mode = detector.region(1920, 1080, 1.4 if cause == 'age' else 1.05,
                                               revision=1 if cause == 'geometry' else 0)
                self.assertEqual(mode, 'searching')

    def test_single_background_hit_does_not_confirm_and_next_miss_resumes_scan(self):
        detector=SpatialDetector()
        detector.region(1920,1080,1)
        self.assertFalse(detector.observe([plane(900,500)],1))
        self.assertEqual(detector.region(1920,1080,1.02)[1],'confirming')
        self.assertFalse(detector.observe([],1.02))
        self.assertEqual(detector.region(1920,1080,1.04)[1],'searching')

    def test_three_associated_hits_confirm_but_large_jump_is_rejected(self):
        detector=SpatialDetector()
        detector.region(1920,1080,1)
        self.assertFalse(detector.observe([plane(900,500)],1))
        self.assertFalse(detector.observe([plane(905,500)],1.02))
        self.assertTrue(detector.observe([plane(910,500)],1.04))
        self.assertEqual(detector.region(1920,1080,1.06)[1],'tracking')
        self.assertFalse(detector.observe([plane(1300,500)],1.06))
        self.assertEqual(detector.region(1920,1080,1.08)[1],'searching')

    def test_low_confidence_hits_cannot_lock_and_persistent_lock_is_bounded(self):
        detector=SpatialDetector()
        detector.region(1920,1080,1)
        for i in range(3):
            self.assertFalse(detector.observe([{**plane(900,500),'confidence':.55}],1+i*.02))
        self.assertEqual(detector.region(1920,1080,1.08)[1],'searching')
        for i in range(60):
            detector.observe([plane(900,500)],1+i*.02)
        # Fresh repeated observations do not make a lock permanent.
        self.assertEqual(detector.region(1920,1080,2.2)[1],'searching')

    def test_search_visits_every_tile_before_repeating(self):
        detector = SpatialDetector()
        regions = [detector.region(1920,1080, i*.02)[0] for i in range(9)]
        self.assertEqual(len(set(regions[:8])), 8)
        self.assertEqual(regions[0], regions[8])

    def test_crop_labels_keep_native_plane_size_and_original_input(self):
        row = dict(width=1920, height=1080,
                   boxes=[dict(class_id=0, x=.5, y=.5, w=12/1920, h=8/1080)])
        crops = list(native_crops(row, 640))
        labels = [box for _, boxes in crops for box in boxes]
        self.assertTrue(labels)
        self.assertTrue(any(abs(b['w']*640-12) < 1e-6 for b in labels))
        self.assertEqual(row['boxes'][0]['w'], 12/1920)
        self.assertTrue(any(not boxes for _, boxes in crops))

    def test_small_frames_are_padded_and_partial_boxes_never_become_background(self):
        row = dict(width=320, height=192,
                   boxes=[dict(class_id=0, x=.5, y=.5, w=.1, h=.1)])
        region, boxes = next(native_crops(row,640))
        self.assertEqual(region, (0,0,320,192))
        self.assertAlmostEqual(boxes[0]['w']*640, 32)
        row = dict(width=1920, height=1080,
                   boxes=[dict(class_id=0, x=639/1920, y=.1, w=20/1920, h=.01)])
        for (x,y,w,h), boxes in native_crops(row,640):
            if x < 649 and x+w > 629 and y < 113 and y+h > 102:
                self.assertTrue(boxes)


if __name__ == '__main__':
    unittest.main()
