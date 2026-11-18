/**
 * Presentation Generator API Client (Enhanced Gamma Multi-Stage Architecture)
 */

import { fetchWithAuth, API_BASE_URL } from './api';

export interface PresentationGenRequest {
  topic: string;
  slide_count?: number;
  tone?: string;
  theme?: string;
  target_audience?: string;
  presentation_type?: string;
  progress_id?: string;
}

export interface GeneratorProgressResponse {
  success: boolean;
  percent: number;
  message: string;
  done: boolean;
  error?: string;
}

export interface SlideCardItem {
  title: string;
  description: string;
}

export interface StatMetricItem {
  number: string;
  badge: string;
  label: string;
}

export interface ChartData {
  chart_type: 'bar' | 'donut' | 'line';
  title: string;
  labels: string[];
  values: number[];
}

export interface SlideOutline {
  slide_number: number;
  title: string;
  subtitle?: string;
  key_takeaway?: string;
  type: string;
  suggested_layout?: string;
  cards?: SlideCardItem[];
  bullets?: string[];
  stat_metrics?: StatMetricItem[];
  chart_data?: ChartData;
  speaker_notes?: string;
}

export interface PresentationOutline {
  outline_id?: string;
  presentation_title: string;
  subtitle?: string;
  topic: string;
  target_audience: string;
  tone?: string;
  seven_cs_applied?: string[];
  slides: SlideOutline[];
}

export interface OutlineResponse {
  success: boolean;
  message: string;
  outline: PresentationOutline;
}

export interface SeedImportResponse {
  success: boolean;
  message: string;
  source_filename?: string;
  outline: PresentationOutline;
}

export interface PresentationGenResponse {
  success: boolean;
  message: string;
  output_filename: string;
  download_url: string;
  slides_generated: number;
  theme: string;
  outline?: PresentationOutline;
}

export interface ThemeItem {
  id: string;
  name: string;
  bg_hex: string;
  title_hex: string;
  accent_hex: string;
  card_hex: string;
}

export async function fetchThemes(): Promise<ThemeItem[]> {
  try {
    const res = await fetch(`${API_BASE_URL}/presentation-generator/themes`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.themes || [];
  } catch {
    return [];
  }
}

export async function generateOutline(
  payload: PresentationGenRequest
): Promise<OutlineResponse> {
  const response = await fetchWithAuth(`${API_BASE_URL}/presentation-generator/outline`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || errorData.error || 'Failed to generate presentation outline');
  }

  return response.json();
}

export async function importSeedFile(
  file: File,
  options?: { slide_count?: number; tone?: string; target_audience?: string; presentation_type?: string; progress_id?: string }
): Promise<SeedImportResponse> {
  const formData = new FormData();
  formData.append('file', file);
  if (options?.slide_count) formData.append('slide_count', String(options.slide_count));
  if (options?.tone) formData.append('tone', options.tone);
  if (options?.target_audience) formData.append('target_audience', options.target_audience);
  if (options?.presentation_type) formData.append('presentation_type', options.presentation_type);
  if (options?.progress_id) formData.append('progress_id', options.progress_id);

  const response = await fetchWithAuth(`${API_BASE_URL}/presentation-generator/import-seed`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || errorData.error || 'Failed to import document seed');
  }

  return response.json();
}

export async function generateFromOutline(payload: {
  outline: PresentationOutline;
  theme: string;
  progress_id?: string;
  custom_overrides?: {
    show_slide_numbers?: boolean;
    confidentiality_tag?: string;
  };
}): Promise<PresentationGenResponse> {
  const response = await fetchWithAuth(`${API_BASE_URL}/presentation-generator/generate-from-outline`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || errorData.error || 'Failed to compile presentation from outline');
  }

  return response.json();
}

export async function generatePresentation(
  payload: PresentationGenRequest
): Promise<PresentationGenResponse> {
  const response = await fetchWithAuth(`${API_BASE_URL}/presentation-generator/generate`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || errorData.error || 'Failed to generate presentation');
  }

  return response.json();
}

export function getGeneratorDownloadUrl(filename: string): string {
  return `${API_BASE_URL}/presentation-generator/download/${encodeURIComponent(filename)}`;
}

export async function pollGeneratorProgress(progressId: string): Promise<GeneratorProgressResponse> {
  const response = await fetchWithAuth(
    `${API_BASE_URL}/presentation-generator/progress/${encodeURIComponent(progressId)}`,
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || 'Could not retrieve generation progress.');
  return data as GeneratorProgressResponse;
}
