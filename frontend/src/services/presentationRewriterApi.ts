import { API_BASE_URL, fetchWithAuth } from './api';

const API_ORIGIN = API_BASE_URL.replace(/\/api\/?$/, '');
export const MAX_PRESENTATION_UPLOAD_BYTES = 50 * 1024 * 1024;

// ─── Mode & Tone Options ─────────────────────────────────────────────────────

export const PROCESSING_MODES = ['quick', 'professional', 'academic', 'refresh'] as const;
export type ProcessingMode = typeof PROCESSING_MODES[number];

export const WRITING_TONES = [
  'professional', 'academic', 'business', 'technical',
  'executive', 'marketing', 'formal', 'simple_english',
] as const;
export type WritingTone = typeof WRITING_TONES[number];

// ─── Response Types ──────────────────────────────────────────────────────────

export interface QualityScores {
  overall_score:       number;
  grade:               string;
  summary:             string;
  category_scores:     Record<string, number>;
  seven_cs_scores:     Record<string, number>;
  seven_cs_evaluation: Record<string, string>;
  strengths:           string[];
  issues_found:        string[];
  recommendations:     string[];
}

export interface PresentationMetadata {
  slide_count:    number;
  width_inches:   number;
  height_inches:  number;
  preservation_check?: {
    slide_count_match: boolean;
    dimensions_match: boolean;
    visually_identical_structure: boolean;
    structural_mismatches: string[];
    text_changes_detected: number;
    package_mismatches: string[];
  };
}

export interface FinalAssessment {
  overall_score: number;
  original_score: number;
  grade: string;
  original_grade: string;
  improvement: number;
  improvement_percentage: number;
  confidence_score: number;
  category_scores: Record<string, number>;
  original_category_scores: Record<string, number>;
  delta_scores: Record<string, number>;
  change_metrics: {
    total_textboxes_checked: number;
    unchanged: number;
    minor_improvements: number;
    moderate_improvements: number;
    major_rewrites: number;
    change_intensity: number;
  };
}

export interface ExecutiveSummary {
  overall_score: number;
  grade: string;
  improvement_percentage: number;
  quality_label: string;
  quality_description: string;
  strengths: Array<{ category: string; score: number }>;
  weaknesses: Array<{ category: string; score: number }>;
  top_improvements: Array<{ category: string; improvement: number }>;
  overall_assessment: string;
  slide_count: number;
  word_count: number;
}

export interface AnalyticsData {
  overall: {
    original_score: number;
    improved_score: number;
    delta: number;
    improvement_percentage: number;
    original_grade: string;
    improved_grade: string;
  };
  categories: Array<{
    category: string;
    original_score: number;
    improved_score: number;
    delta: number;
    status: string;
  }>;
  summary: {
    total_categories: number;
    improved: number;
    declined: number;
    stable: number;
    improvement_rate: number;
  };
}

export interface RecommendationsData {
  recommendations: Array<{
    category: string;
    priority: string;
    recommendation: string;
    reason: string;
    expected_impact: string;
  }>;
  categorized: Record<string, Array<{
    category: string;
    priority: string;
    recommendation: string;
    reason: string;
    expected_impact: string;
  }>>;
  total_recommendations: number;
  high_priority_count: number;
  medium_priority_count: number;
  low_priority_count: number;
}

export interface PresentationStatistics {
  slide_count: number;
  total_words: number;
  reading_time: string;
  speaking_time: string;
  estimated_duration: string;
  average_words_per_slide: number;
}

export interface PresentationRewriterResponse {
  success:          boolean;
  download_url:     string;
  output_filename:  string;
  slides_processed: number;
  processing_time:  string;
  quality_scores:   QualityScores;
  improvements:     string[];
  processing_steps: string[];
  metadata:         PresentationMetadata;
  mode?:            string;
  tone?:            string;
  final_assessment?: FinalAssessment;
  executive_summary?: ExecutiveSummary;
  analytics?:       AnalyticsData;
  recommendations?: RecommendationsData;
  statistics?:      PresentationStatistics;
  message:          string;
}

export interface PresentationAnalysisResponse {
  success:          boolean;
  quality_scores:   QualityScores;
  slides_analysed:  number | null;
  processing_steps: string[];
  processing_time:  string;
  message:          string;
  analyses?:        any;
}

export interface SlideComparisonReport {
  slide_number: number;
  title: string;
  textboxes: Array<{
    shape_index: number;
    shape_name: string;
    original_paragraphs: string[];
    improved_paragraphs: string[];
  }>;
  tables: Array<{
    shape_index: number;
    cells: Array<{
      row_index: number;
      column_index: number;
      original_paragraphs: string[];
      improved_paragraphs: string[];
    }>;
  }>;
  charts: Array<{
    shape_index: number;
    original_title_paragraphs: string[];
    improved_title_paragraphs: string[];
  }>;
}

export interface ReportData {
  output_filename: string;
  original_filename: string;
  slide_count: number;
  quality_scores: QualityScores;
  slides: SlideComparisonReport[];
  generated_at: string | null;
  final_assessment?: FinalAssessment;
  executive_summary?: ExecutiveSummary;
  analytics?: AnalyticsData;
  recommendations?: RecommendationsData;
}

export interface ProgressResponse {
  success: boolean;
  step_index: number;
  total_steps: number;
  percent: number;
  message: string;
  done: boolean;
}

const parseResponse = async (response: Response, fallbackMessage: string) => {
  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json')
    ? await response.json()
    : { success: false, error: 'ResponseError', message: await response.text() };
  if (!response.ok || !data.success) {
    throw new Error(data.message || data.error || fallbackMessage);
  }
  return data;
};

const normaliseDownloadUrl = (downloadUrl: string): string => (
  downloadUrl.startsWith('http') ? downloadUrl : `${API_ORIGIN}${downloadUrl}`
);

// ─── API Functions ───────────────────────────────────────────────────────────

/**
 * Submit a PPTX file for full AI rewrite.
 * Returns a download URL + quality analysis of the original.
 *
 * @param mode - Processing mode: quick | professional (default) | academic
 * @param tone - Writing tone: professional (default) | academic | business | technical | ...
 */
export const rewritePresentation = async (
  file: File,
  mode: ProcessingMode = 'professional',
  tone: WritingTone = 'professional',
): Promise<PresentationRewriterResponse> => {
  if (!file.name.toLowerCase().endsWith('.pptx')) {
    throw new Error('Only .pptx files can be rewritten.');
  }
  if (file.size > MAX_PRESENTATION_UPLOAD_BYTES) {
    throw new Error('File too large. Maximum allowed size is 50 MB.');
  }
  const formData = new FormData();
  formData.append('file', file);
  formData.append('mode', mode);
  formData.append('tone', tone);

  const response = await fetchWithAuth(
    `${API_BASE_URL}/presentation-rewriter/submit`,
    { method: 'POST', body: formData },
  );

  const data = await parseResponse(response, 'Presentation rewriting failed.') as PresentationRewriterResponse;
  return { ...data, download_url: normaliseDownloadUrl(data.download_url) };
};

/**
 * Submit a PPTX or PDF for analysis only (no rewritten file produced).
 */
export const analyzePresentation = async (
  file: File,
): Promise<PresentationAnalysisResponse> => {
  const extension = file.name.toLowerCase().split('.').pop();
  if (extension !== 'pptx' && extension !== 'pdf') {
    throw new Error('Analysis supports .pptx and .pdf files.');
  }
  if (file.size > MAX_PRESENTATION_UPLOAD_BYTES) {
    throw new Error('File too large. Maximum allowed size is 50 MB.');
  }
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetchWithAuth(
    `${API_BASE_URL}/presentation-rewriter/analyze`,
    { method: 'POST', body: formData },
  );

  return await parseResponse(response, 'Presentation analysis failed.') as PresentationAnalysisResponse;
};

/**
 * Poll the backend for real-time processing progress of a submitted file.
 */
export const pollProgress = async (
  filename: string,
): Promise<ProgressResponse> => {
  const response = await fetch(
    `${API_BASE_URL}/presentation-rewriter/progress/${encodeURIComponent(filename)}`,
  );
  const data = await parseResponse(response, 'Failed to fetch progress.');
  return data as ProgressResponse;
};

/**
 * Fetch the detailed per-slide comparison report for a completed rewrite.
 */
export const fetchSlideReport = async (
  filename: string,
): Promise<{ success: boolean; report: ReportData }> => {
  const response = await fetch(
    `${API_BASE_URL}/presentation-rewriter/report/${encodeURIComponent(filename)}`,
  );
  return await parseResponse(response, 'Failed to fetch slide report.');
};

/**
 * Get the download URL for the PDF version of the analysis report.
 */
export const getReportPdfUrl = (filename: string): string => {
  return `${API_BASE_URL}/presentation-rewriter/report/${encodeURIComponent(filename)}/pdf`;
};

/**
 * Resolve download URLs to full API origin if relative (ISSUE-18)
 */
export const getDownloadUrl = (path: string): string => {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  const rootUrl = API_BASE_URL.replace(/\/api\/?$/, '');
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${rootUrl}${cleanPath}`;
};
