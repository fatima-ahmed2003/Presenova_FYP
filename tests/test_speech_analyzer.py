import importlib.util
import io
import array
import math
import os
import shutil
import sys
import subprocess
import types
import uuid
import wave

import pytest
from flask import Flask
from flask_jwt_extended import JWTManager


@pytest.fixture
def speech_environment(monkeypatch, tmp_path):
    models_stub = types.ModuleType("models")

    class Upload:
        records = []

        @classmethod
        def create(cls, **values):
            cls.records.append(values)
            return types.SimpleNamespace(id=f"speech-upload-{len(cls.records)}")

    class Report:
        records = []

        @classmethod
        def create(cls, **values):
            cls.records.append(values)
            return types.SimpleNamespace(id=f"speech-report-{len(cls.records)}")

    models_stub.Upload = Upload
    models_stub.Report = Report
    monkeypatch.setitem(sys.modules, "models", models_stub)

    module_name = f"_speech_analyzer_test_{uuid.uuid4().hex}"
    module_path = os.path.join(os.path.dirname(__file__), "..", "phase_four.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    phase_four = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, phase_four)
    spec.loader.exec_module(phase_four)
    monkeypatch.setattr(
        phase_four,
        "os",
        types.SimpleNamespace(
            path=os.path,
            getcwd=lambda: str(tmp_path),
            makedirs=os.makedirs,
            remove=os.remove,
            exists=os.path.exists,
            getsize=os.path.getsize,
        ),
    )

    import ai_evaluator
    from services.language_tool_service import GrammarCheckResult

    monkeypatch.setattr(ai_evaluator._gemini_provider, "is_available", lambda: False)
    monkeypatch.setattr(
        ai_evaluator._gemini_provider,
        "generate_structured",
        lambda *_args, **_kwargs: pytest.fail("Gemini must not be called in offline tests"),
    )
    monkeypatch.setattr(
        ai_evaluator,
        "check_grammar",
        lambda _text: GrammarCheckResult(status="disabled"),
    )

    def offline_speech_evaluation(
        transcript,
        speech_speed_wpm,
        filler_count,
        filler_percentage,
        repetition_count,
        duration_seconds,
    ):
        return {
            "evaluation_method": "gemini",
            "overall_score": 84,
            "category_scores": {
                "Structure": 82,
                "Clarity": 84,
                "Persuasion": 80,
                "Content_Quality": 85,
                "Call_to_Action": 81,
            },
            "seven_cs_scores": {
                "Clear": 84,
                "Concise": 82,
                "Correct": 80,
                "Complete": 81,
                "Courteous": 85,
                "Concrete": 83,
                "Coherent": 82,
            },
            "seven_cs_evaluation": {
                key: f"Evaluated from the supplied transcript ({len(transcript.split())} words)."
                for key in (
                    "Clear",
                    "Concise",
                    "Correct",
                    "Complete",
                    "Courteous",
                    "Concrete",
                    "Coherent",
                )
            },
            "strengths": ["The transcript includes a clear main point."],
            "recommendations": ["Support the main point with a specific example."],
            "detailed_feedback": (
                f"The evaluation reviewed the supplied {len(transcript.split())}-word transcript."
            ),
            "improved_text": transcript,
            "grammar_score": None,
            "grammar_issues": [],
            "grammar_issues_count": 0,
            "grammar_check_status": "disabled",
        }

    monkeypatch.setattr(
        phase_four, "run_gemini_speech_analysis", offline_speech_evaluation
    )

    app = Flask(f"speech-test-{uuid.uuid4().hex}")
    app.config.update(TESTING=True, JWT_SECRET_KEY="speech-test-only-secret")
    JWTManager(app)
    app.register_blueprint(phase_four.phase_four_bp)
    return app.test_client(), phase_four, Upload, Report, ai_evaluator


def _speech_context():
    return {
        "speech_speed_wpm": 140,
        "filler_count": 2,
        "filler_percentage": 10.0,
        "repetition_count": 1,
        "duration_seconds": 12,
    }


def _make_test_wav(duration_seconds=4, include_speech=False):
    sample_rate = 16000
    pcm = array.array("h")
    for index in range(sample_rate * duration_seconds):
        seconds = index / sample_rate
        is_voice_frame = include_speech and seconds % 1 < 0.7
        amplitude = 5000 if is_voice_frame else 8
        pcm.append(int(amplitude * math.sin(2 * math.pi * 220 * seconds)))
    if sys.byteorder != "little":
        pcm.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return output.getvalue()


def test_speech_evaluator_does_not_create_fallback_scores(speech_environment):
    _client, _phase_four, _upload, _report, evaluator = speech_environment
    report = evaluator.evaluate_7cs(
        "Today our survey measured 42 residents. Please approve the trial next month.",
        "speech",
        _speech_context(),
    )

    assert report["evaluation_method"] == "unavailable"
    assert {
        "overall_score",
        "category_scores",
        "seven_cs_scores",
        "seven_cs_evaluation",
    }.isdisjoint(report)
    assert report["grammar_score"] is None
    assert report["grammar_check_status"] == "disabled"


def test_speech_evaluator_does_not_fallback_to_grammar_only_scores(
    speech_environment, monkeypatch
):
    _client, _phase_four, _upload, _report, evaluator = speech_environment
    from services.language_tool_service import GrammarCheckResult

    issue = {
        "rule_id": "TEST_RULE",
        "message": "A test grammar issue.",
        "context": "sample transcript",
        "offset": 0,
        "length": 6,
        "replacements": [],
    }
    monkeypatch.setattr(
        evaluator,
        "check_grammar",
        lambda _text: GrammarCheckResult([issue], status="success"),
    )

    report = evaluator.evaluate_7cs(
        "The team measured 12 results. Please review them today.",
        "speech",
        _speech_context(),
    )

    assert report["evaluation_method"] == "unavailable"
    assert report["grammar_check_status"] == "success"
    assert report["grammar_score"] is not None
    assert "overall_score" not in report
    assert "seven_cs_scores" not in report


def test_text_endpoint_returns_mocked_analysis_and_validates_input(
    speech_environment,
):
    client, _phase_four, _upload, report_store, _evaluator = speech_environment
    transcript = (
        "Our survey measured 30 visits. The results support an earlier opening time. "
        "Please review the change next week."
    )
    response = client.post(
        "/api/analyze-speech",
        json={"text": transcript, "duration_seconds": 30},
    )

    assert response.status_code == 200
    report = response.get_json()
    assert report["transcript"] == transcript
    assert report["word_count"] == len(transcript.split())
    assert report["category_scores"]
    assert report["recommendations"]
    assert report_store.records[0]["report_json"]["transcript"] == transcript

    invalid_text = client.post(
        "/api/analyze-speech", json={"text": 12, "duration_seconds": 30}
    )
    invalid_duration = client.post(
        "/api/analyze-speech", json={"text": transcript, "duration_seconds": "invalid"}
    )
    zero_duration = client.post(
        "/api/analyze-speech", json={"text": transcript, "duration_seconds": 0}
    )
    malformed_json = client.post(
        "/api/analyze-speech",
        data="{not json",
        content_type="application/json",
    )
    assert invalid_text.status_code == 400
    assert invalid_duration.status_code == 400
    assert zero_duration.status_code == 400
    assert malformed_json.status_code == 400


def test_text_endpoint_does_not_return_heuristic_fallback_scores(
    speech_environment, monkeypatch
):
    client, phase_four, _upload, report_store, _evaluator = speech_environment
    monkeypatch.setattr(
        phase_four,
        "run_gemini_speech_analysis",
        lambda **_kwargs: {
            "evaluation_method": "heuristic_fallback",
            "overall_score": 80,
            "category_scores": {"Clarity": 80},
            "seven_cs_scores": {
                "Clear": 80,
                "Concise": 80,
                "Correct": 80,
                "Complete": 80,
                "Courteous": 80,
                "Concrete": 80,
                "Coherent": 80,
            },
        },
    )

    response = client.post(
        "/api/analyze-speech",
        json={"text": "The team measured twelve results. Please review them today.", "duration_seconds": 10},
    )

    assert response.status_code == 503
    result = response.get_json()
    assert result["error"] == "SpeechScoringUnavailable"
    assert "overall_score" not in result
    assert not report_store.records


def test_audio_upload_uses_mocked_transcription_and_handles_errors(
    speech_environment, monkeypatch, caplog, tmp_path
):
    client, phase_four, upload_store, report_store, _evaluator = speech_environment
    caplog.set_level("INFO")
    transcript = (
        "Every morning, I wake up at six thirty and make coffee. Then I prepare oatmeal "
        "and feed my cat before getting dressed. I leave home at seven forty-five and "
        "take the bus to the office. During lunch, I walk around the park for fifteen "
        "minutes. After work, I buy groceries and cook dinner with my partner. In the "
        "evening, I call my mother, stretch for twenty minutes, and read before going "
        "to bed at ten thirty. On weekends, I clean the apartment, plan meals for the "
        "week, and visit my neighbors. It helps me stay organized and relaxed. Um, I "
        "sometimes forget lunch, so I pack it the night before."
    )
    transcription_request = {}
    evaluated_input = {}
    offline_evaluator = phase_four.run_gemini_speech_analysis

    def capture_evaluation(transcript, **metrics):
        evaluated_input["transcript"] = transcript
        evaluated_input.update(metrics)
        return offline_evaluator(transcript, **metrics)

    monkeypatch.setattr(
        phase_four, "run_gemini_speech_analysis", capture_evaluation
    )

    class FakeGroq:
        class Audio:
            class Transcriptions:
                def create(self, **_kwargs):
                    transcription_request.update(_kwargs)
                    return types.SimpleNamespace(
                        text=transcript,
                        segments=[
                            types.SimpleNamespace(no_speech_prob=0.1),
                            types.SimpleNamespace(no_speech_prob=0.2),
                        ],
                    )

            transcriptions = Transcriptions()

        audio = Audio()

    phase_four.groq_client = FakeGroq()
    spoken_audio = _make_test_wav(duration_seconds=52, include_speech=True)
    response = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(spoken_audio), "sample.wav"),
            "duration_seconds": "52",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    report = response.get_json()
    assert report["transcript"] == transcript
    assert report["word_count"] == len(transcript.split())
    assert report["duration_seconds"] == 52
    assert report["speech_speed_wpm"] == round(len(transcript.split()) / 52 * 60, 2)
    assert report["filler_words_count"] == 1
    assert evaluated_input["transcript"] == transcript
    assert report["category_scores"]
    assert report["seven_cs_scores"]
    assert report["seven_cs_evaluation"]
    assert report["recommendations"]
    assert report["detailed_feedback"]
    assert report["evaluation_method"] == "gemini"
    assert transcription_request["file"] == (
        "sample.wav",
        spoken_audio,
    )
    assert transcription_request["model"] == "whisper-large-v3"
    assert transcription_request["response_format"] == "verbose_json"
    assert "prompt" not in transcription_request
    assert "I'm, like, thinking about it" not in report["transcript"]
    assert report["improved_text"].startswith("Every morning")
    audio_bytes = len(spoken_audio)
    assert (
        f"speech_audio_diagnostics=phase_four_v5 status=received "
        f"audio_bytes={audio_bytes} duration_seconds=52"
    ) in caplog.text
    assert (
        f"speech_transcription_diagnostics=phase_four_v5 status=success "
        f"audio_bytes={audio_bytes} duration_seconds=52 "
        f"model=whisper-large-v3 segment_count=2 avg_no_speech_prob=0.150 "
        f"transcript_words={len(transcript.split())}"
    ) in caplog.text
    assert transcript in caplog.text
    assert "speech_analyzer_debug status=success" in caplog.text
    assert "content_type=audio/wav" in caplog.text
    assert "rms_gate=speech" in caplog.text
    assert "rms_threshold=" in caplog.text
    assert "rejection_check=none" in caplog.text
    debug_audio = tmp_path / "instance" / "debug" / "last_speech.wav"
    assert debug_audio.read_bytes() == spoken_audio
    assert "sample.wav" not in caplog.text
    assert len(upload_store.records) == len(report_store.records) == 1

    invalid_duration = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(b"offline audio fixture"), "invalid-duration.wav"),
            "duration_seconds": "invalid",
        },
        content_type="multipart/form-data",
    )
    empty_audio = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(b""), "empty.wav"),
            "duration_seconds": "20",
        },
        content_type="multipart/form-data",
    )
    assert invalid_duration.status_code == 400
    assert empty_audio.status_code == 400
    assert len(report_store.records) == 1


def test_audio_endpoint_does_not_return_heuristic_fallback_scores(
    speech_environment, monkeypatch
):
    client, phase_four, upload_store, report_store, _evaluator = speech_environment
    transcript = "The team measured twelve results. Please review them today."

    class MockGroq:
        class Audio:
            class Transcriptions:
                def create(self, **_kwargs):
                    return types.SimpleNamespace(
                        text=transcript,
                        segments=[types.SimpleNamespace(no_speech_prob=0.1)],
                    )

            transcriptions = Transcriptions()

        audio = Audio()

    phase_four.groq_client = MockGroq()
    monkeypatch.setattr(
        phase_four,
        "run_gemini_speech_analysis",
        lambda **_kwargs: {
            "evaluation_method": "heuristic_fallback",
            "overall_score": 80,
            "category_scores": {"Clarity": 80},
            "seven_cs_scores": {
                "Clear": 80,
                "Concise": 80,
                "Correct": 80,
                "Complete": 80,
                "Courteous": 80,
                "Concrete": 80,
                "Coherent": 80,
            },
        },
    )

    response = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(_make_test_wav(include_speech=True)), "fallback.wav"),
            "duration_seconds": "4",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    result = response.get_json()
    assert result["error"] == "SpeechScoringUnavailable"
    assert "overall_score" not in result
    assert not upload_store.records
    assert not report_store.records


def test_silent_audio_is_rejected_before_transcription_or_scoring(
    speech_environment, monkeypatch, caplog
):
    client, phase_four, upload_store, report_store, _evaluator = speech_environment
    caplog.set_level("INFO")

    class UnexpectedGroq:
        class Audio:
            class Transcriptions:
                def create(self, **_kwargs):
                    pytest.fail("Silent audio must be rejected before Groq.")

            transcriptions = Transcriptions()

        audio = Audio()

    phase_four.groq_client = UnexpectedGroq()
    monkeypatch.setattr(
        phase_four,
        "run_gemini_speech_analysis",
        lambda **_kwargs: pytest.fail("Silent audio must not be scored."),
    )
    silent_audio = _make_test_wav(duration_seconds=4)
    response = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(silent_audio), "silence.wav"),
            "duration_seconds": "4",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    report = response.get_json()
    assert report["error"] == "NoSpeechDetected"
    assert report["message"] == "No speech detected. Please record again."
    assert {
        "overall_score",
        "speech_speed_wpm",
        "seven_cs_scores",
        "clarity_score",
    }.isdisjoint(report)
    assert (
        f"speech_signal_diagnostics=phase_four_v5 status=silence "
        f"audio_bytes={len(silent_audio)} duration_seconds=4"
    ) in caplog.text
    assert not upload_store.records
    assert not report_store.records


def test_audio_transcription_failure_never_creates_a_fabricated_report(
    speech_environment, caplog
):
    client, phase_four, upload_store, report_store, _evaluator = speech_environment
    caplog.set_level("INFO")

    class FailedGroq:
        class Audio:
            class Transcriptions:
                def create(self, **_kwargs):
                    raise TimeoutError("offline test timeout")

            transcriptions = Transcriptions()

        audio = Audio()

    phase_four.groq_client = FailedGroq()
    spoken_audio = _make_test_wav(duration_seconds=20, include_speech=True)
    response = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(spoken_audio), "timeout.wav"),
            "duration_seconds": "20",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "TranscriptionUnavailable"
    assert (
        "speech_transcription_diagnostics=phase_four_v5 status=provider_error "
        f"audio_bytes={len(spoken_audio)} duration_seconds=20 "
        "model=whisper-large-v3 segment_count=0 avg_no_speech_prob=unavailable "
        "transcript_words=0"
    ) in caplog.text
    assert "timeout.wav" not in caplog.text
    assert not upload_store.records
    assert not report_store.records


@pytest.mark.parametrize(
    ("transcript", "probabilities", "expected_status"),
    [
        ("I'm, like, thinking about it, actually", [0.75, 0.9], "no_speech"),
        ("Thank you for watching today", [0.95, 0.95, 0.1], "no_speech"),
        ("The team completed its work earlier today.", [0.9, 0.5], "no_speech"),
        ("", [0.1], "unreliable_transcript"),
        ("yes okay", [0.1], "unreliable_transcript"),
        ("The team completed its work earlier today.", [], "unreliable_transcript"),
    ],
)
def test_unreliable_whisper_output_is_rejected_without_scoring(
    speech_environment, monkeypatch, caplog, transcript, probabilities, expected_status
):
    client, phase_four, upload_store, report_store, _evaluator = speech_environment
    caplog.set_level("INFO")

    class MockGroq:
        class Audio:
            class Transcriptions:
                def create(self, **kwargs):
                    assert kwargs["response_format"] == "verbose_json"
                    assert "prompt" not in kwargs
                    return types.SimpleNamespace(
                        text=transcript,
                        segments=[
                            types.SimpleNamespace(no_speech_prob=value)
                            for value in probabilities
                        ],
                    )

            transcriptions = Transcriptions()

        audio = Audio()

    phase_four.groq_client = MockGroq()
    monkeypatch.setattr(
        phase_four,
        "run_gemini_speech_analysis",
        lambda **_kwargs: pytest.fail("Unreliable transcription must not be scored."),
    )
    spoken_audio = _make_test_wav(duration_seconds=4, include_speech=True)
    response = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(spoken_audio), "unreliable.wav"),
            "duration_seconds": "4",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    report = response.get_json()
    assert report["error"] == "NoSpeechDetected"
    assert report["message"] == "No speech detected. Please record again."
    assert not {
        "overall_score",
        "speech_speed_wpm",
        "seven_cs_scores",
        "clarity_score",
    }.intersection(report)
    assert f"status={expected_status}" in caplog.text
    if transcript:
        assert transcript in caplog.text
    assert "speech_analyzer_debug" in caplog.text
    assert not upload_store.records
    assert not report_store.records


def test_windows_tts_audio_prints_route_diagnostics_offline(
    speech_environment, monkeypatch, caplog, tmp_path
):
    client, phase_four, _upload_store, _report_store, _evaluator = speech_environment
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if sys.platform != "win32" or not powershell:
        pytest.skip("Windows PowerShell TTS is available only on Windows.")

    audio_path = tmp_path / "windows_tts_speech.wav"
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$synth.SetOutputToWaveFile($env:SPEECH_TTS_OUTPUT); "
        "$synth.Speak('This is an offline speech test. I am speaking clearly "
        "so the analyzer can measure the recording and print complete local "
        "diagnostics for this ten second sample.'); "
        "$synth.Dispose();"
    )
    tts_environment = os.environ.copy()
    tts_environment["SPEECH_TTS_OUTPUT"] = str(audio_path)
    tts_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=tts_environment,
    )
    if tts_result.returncode != 0 or not audio_path.exists():
        pytest.skip("Windows offline speech synthesis is unavailable.")

    with wave.open(str(audio_path), "rb") as tts_wav:
        audio_duration = tts_wav.getnframes() / tts_wav.getframerate()
    assert 7 <= audio_duration <= 15
    spoken_audio = audio_path.read_bytes()
    transcript = (
        "This is an offline speech test spoken clearly for local analyzer diagnostics."
    )

    class MockGroq:
        class Audio:
            class Transcriptions:
                def create(self, **_kwargs):
                    return types.SimpleNamespace(
                        text=transcript,
                        segments=[
                            types.SimpleNamespace(
                                no_speech_prob=0.1,
                                avg_logprob=-0.2,
                            )
                        ],
                    )

            transcriptions = Transcriptions()

        audio = Audio()

    phase_four.groq_client = MockGroq()
    caplog.set_level("INFO")
    response = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(spoken_audio), "speech.wav", "audio/wav"),
            "duration_seconds": "10",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert "speech_analyzer_debug status=success" in caplog.text
    assert "sample_rate=22050" in caplog.text
    assert "channels=1" in caplog.text
    assert "rms_overall=" in caplog.text
    assert "rms_gate=speech" in caplog.text
    assert "no_speech_prob': 0.1" in caplog.text
    assert "avg_logprob': -0.2" in caplog.text
    assert "avg_no_speech_prob=0.100000" in caplog.text
    assert repr(transcript) in caplog.text
    assert "rejection_check=none" in caplog.text
    assert (
        tmp_path / "instance" / "debug" / "last_speech.wav"
    ).read_bytes() == spoken_audio


@pytest.mark.parametrize(
    "transcription",
    [None, types.SimpleNamespace(text=123), types.SimpleNamespace()],
)
def test_malformed_transcription_is_unavailable_without_a_report(
    speech_environment, transcription
):
    client, phase_four, upload_store, report_store, _evaluator = speech_environment

    class MalformedGroq:
        class Audio:
            class Transcriptions:
                def create(self, **_kwargs):
                    return transcription

            transcriptions = Transcriptions()

        audio = Audio()

    phase_four.groq_client = MalformedGroq()
    spoken_audio = _make_test_wav(duration_seconds=20, include_speech=True)
    response = client.post(
        "/api/analyze-audio",
        data={
            "file": (io.BytesIO(spoken_audio), "malformed.wav"),
            "duration_seconds": "20",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "TranscriptionUnavailable"
    assert not upload_store.records
    assert not report_store.records
