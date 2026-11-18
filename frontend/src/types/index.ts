/**
 * TypeScript Interfaces & Types
 * Defines the structure of API responses and data models
 */

// Phase 1: Authentication

export interface User {
  id: string;
  name: string;
  email: string;
  created_at: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface SignupRequest {
  name: string;
  email: string;
  password: string;
}

// Phase 2: Document Analysis Response
export interface CategoryScores {
  Structure: number;
  Clarity: number;
  Persuasion?: number;
  Content_Quality?: number;
  Call_to_Action?: number;
  Conciseness?: number;
  Completeness?: number;
  Correctness?: number;
  Coherence?: number;
  Consistency?: number;
  [key: string]: number | undefined;
}

export interface SevenCsEvaluationItem {
  status: string;
  feedback: string;
}

export type SevenCsEvaluation = {
  Clear: string | SevenCsEvaluationItem;
  Concise: string | SevenCsEvaluationItem;
  Correct: string | SevenCsEvaluationItem;
  Complete: string | SevenCsEvaluationItem;
  Courteous: string | SevenCsEvaluationItem;
  Concrete: string | SevenCsEvaluationItem;
} & (
  | { Coherent: string | SevenCsEvaluationItem; Consistent?: string | SevenCsEvaluationItem }
  | { Coherent?: string | SevenCsEvaluationItem; Consistent: string | SevenCsEvaluationItem }
);

export type SevenCsScores = {
  Clear: number;
  Concise: number;
  Correct: number;
  Complete: number;
  Courteous: number;
  Concrete: number;
  Coherent?: number | null;
  Consistent?: number | null;
};

export interface SevenCsEvidenceIssue {
  unit_no: number;
  rule: string;
  issue: string;
  offset: number;
  original: string;
  suggestions: string[];
  category: string;
  severity_penalty: number;
}

export interface SevenCsDimensionEvidence {
  issue_count: number;
  issues: SevenCsEvidenceIssue[];
  per_unit?: Array<{ unit_no: number; penalty: number; score: number }>;
  document_penalty?: number;
  status?: string;
  score?: number;
  reason?: string;
}

export interface DocumentUnit {
  unit_no: number;
  type: 'slide' | 'page' | 'section';
  title: string;
  text: string;
  bullets: string[];
  word_count: number;
  min_font_size: number | null;
  has_image: boolean;
}

export interface DocumentQualityIssue {
  unit_no: number;
  rule: string;
  issue: string;
  offset: number;
  original: string;
  suggestions: string[];
  category: string;
}

export interface DocumentAiSuggestion {
  unit_no: number;
  original_line: string;
  problem: string;
  fix: string;
  severity: 'low' | 'medium' | 'high';
}

export interface DocumentCoherence {
  score: number;
  reason: string;
}

export interface ContextAnalysis {
  context_accuracy_score?: number | null;
  is_context_accurate?: boolean | null;
  factual_correctness_summary?: string;
  topic?: string;
  purpose?: string;
  intended_audience?: string;
  major_arguments?: string[];
  coverage_gaps?: string[];
  inaccuracies_detected?: string[];
  context_based_changes?: string[];
}

export interface DocumentScoreRubricItem {
  weight_percent: number;
  score: number;
}

export type DocumentEvaluationMethod =
  | 'gemini'
  | 'heuristic_fallback'
  | 'deterministic_rules';

export type GrammarCheckStatus =
  | 'success'
  | 'disabled'
  | 'skipped'
  | 'timeout'
  | 'connection_error'
  | 'http_error'
  | 'invalid_response'
  | 'error';

export interface AnalysisReport {
  status: string;
  overall_score: number;
  document_name: string;
  detailed_feedback: string;
  category_scores: CategoryScores;
  grammar_score?: number | null;
  grammar_check_status?: GrammarCheckStatus;
  evaluation_method?: DocumentEvaluationMethod;
  score_rubric?: Record<string, DocumentScoreRubricItem>;
  seven_cs_evaluation: SevenCsEvaluation;
  seven_cs_scores?: SevenCsScores;
  seven_cs_evidence?: Record<string, SevenCsDimensionEvidence>;
  document_units?: DocumentUnit[];
  quality_checks?: {
    issues: DocumentQualityIssue[];
    language_tool?: {
      skipped: boolean;
      units: Array<{ unit_no: number; status: string; reason?: string }>;
    };
    readability?: Array<{
      unit_no: number;
      flesch_reading_ease: number | null;
      flesch_kincaid_grade: number | null;
    }>;
  };
  ai_suggestions?: DocumentAiSuggestion[];
  coherence?: DocumentCoherence | null;
  ai_suggestions_unavailable?: boolean;
  ai_suggestions_provider?: 'groq' | 'gemini' | null;
  ai_suggestions_cached?: boolean;
  completeness_checks?: Record<
    string,
    { present: boolean; unit_numbers: number[] }
  >;
  context_analysis?: ContextAnalysis;
  recommendations: string[];
  original_text?: string;
  improved_text?: string;
  strengths?: string[];
  analysis_timestamp: string;
}

// Phase 4: Speech Analysis Response
export interface SpeechMetrics {
  status: string;
  word_count: number;
  filler_words_count: number;
  filler_words_percentage: number;
  speech_speed_wpm: number;
  repetition_count: number;
  duration_seconds: number;
  actionable_feedback: string[];
  clarity_score: number;
  transcript?: string;
  seven_cs_evaluation?: SevenCsEvaluation;
  overall_score?: number;
  category_scores?: CategoryScores;
  seven_cs_scores?: SevenCsScores;
  strengths?: string[];
  recommendations?: string[];
  detailed_feedback?: string;
  improved_text?: string;
}

// Phase 5: AI Coach Chat Response
export interface ChatResponse {
  status: string;
  ai_response: string;
  message_id: string;
  timestamp: string;
}

// Phase 5: Chat Message History
export interface ChatMessage {
  id: string;
  role: 'user' | 'ai';
  content: string;
  timestamp: string;
}

export interface ContextReport {
  phase: string;
  analysis?: AnalysisReport | SpeechMetrics;
  session_id?: string;
  v1Analysis?: any;
  v2Analysis?: any;
  v1Text?: string;
  v2Text?: string;
  comparison?: any;
  v1Report?: any;
  v2Report?: any;
  v1Transcript?: string;
  v2Transcript?: string;
  comparisonReport?: any;
}

// API Error Response
export interface ApiError {
  error: string;
  message: string;
  status: number;
}

// Phase 2: Document Version Comparison (NO LONGER USED - kept for reference)
// export interface ComparisonReport {
//   score_difference: number;
//   key_improvements: string[];
//   remaining_issues: string[];
//   synthesis_summary: string;
// }
