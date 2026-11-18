from types import SimpleNamespace

import phase_live


def _landmark(x=0.5, y=0.5, visibility=1.0, presence=1.0):
    return SimpleNamespace(x=x, y=y, visibility=visibility, presence=presence)


def test_gaze_uses_iris_landmarks_and_three_second_calibration(monkeypatch):
    points = [_landmark() for _ in range(478)]
    # Eye corners/lids and iris centers: centered iris in each eye.
    for index, point in {
        33: _landmark(.35, .45), 133: _landmark(.45, .45),
        362: _landmark(.55, .45), 263: _landmark(.65, .45),
        159: _landmark(.40, .43), 145: _landmark(.40, .47),
        386: _landmark(.60, .43), 374: _landmark(.60, .47),
    }.items():
        points[index] = point
    for index in range(468, 473):
        points[index] = _landmark(.40, .45)
    for index in range(473, 478):
        points[index] = _landmark(.60, .45)

    horizontal, vertical = phase_live._gaze_ratios(points)
    assert abs(horizontal - 0.5) < 1e-6
    assert abs(vertical - 0.5) < 1e-6

    times = iter([10.0, 10.5, 11.0, 12.0, 13.1, 13.2])
    monkeypatch.setattr(phase_live.time, 'monotonic', lambda: next(times))
    session = SimpleNamespace(id='gaze-test')
    for _ in range(4):
        score, status, _confidence = phase_live._calibrate_gaze(session, horizontal, vertical)
        assert score is None
        assert status == 'calibrating'
    score, status, confidence = phase_live._calibrate_gaze(session, horizontal, vertical)
    assert status == 'calibrated'
    assert score == 100
    assert confidence is not None


def test_head_pose_matrix_and_rolling_variation(monkeypatch):
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    assert phase_live._estimate_head_pose(identity) == (0.0, 0.0)
    times = iter([20.0, 20.2, 20.4, 20.6, 20.8])
    monkeypatch.setattr(phase_live.time, 'monotonic', lambda: next(times))
    session = SimpleNamespace(id='head-test')
    scores = []
    for yaw in (0, 4, 10, 18, 25):
        pose = (yaw, 0.0)
        score, confidence = phase_live._measure_head_movement(session, pose)
        if score is not None:
            scores.append(score)
            assert confidence is not None
    assert scores and scores[-1] < 100


def test_pose_posture_score_uses_shoulders_hips_and_head_alignment():
    points = [_landmark() for _ in range(25)]
    points[0] = _landmark(.50, .25)
    points[11] = _landmark(.40, .40)
    points[12] = _landmark(.60, .40)
    points[23] = _landmark(.42, .72)
    points[24] = _landmark(.58, .72)
    score, confidence = phase_live._estimate_posture_score(points, 640, 480)
    assert score is not None and score >= 90
    assert confidence == 100
    points[11] = _landmark(.40, .35)
    points[12] = _landmark(.60, .46)
    tilted_score, _ = phase_live._estimate_posture_score(points, 640, 480)
    assert tilted_score < score


def test_expression_mapping_uses_blendshape_scores():
    def cats(**scores):
        return [SimpleNamespace(category_name=name, score=value) for name, value in scores.items()]

    smile, smile_conf = phase_live._map_expression(cats(mouthSmileLeft=.8, mouthSmileRight=.7))
    tense, tense_conf = phase_live._map_expression(cats(browDownLeft=.7, browDownRight=.6))
    neutral, neutral_conf = phase_live._map_expression(cats(mouthSmileLeft=.05, mouthSmileRight=.04))
    assert smile == 'smile' and smile_conf > 0
    assert tense == 'tense' and tense_conf > 0
    assert neutral == 'neutral' and neutral_conf > 0
