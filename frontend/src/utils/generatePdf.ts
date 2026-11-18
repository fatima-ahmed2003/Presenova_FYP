import jsPDF from 'jspdf';
import { AnalysisReport } from '../types/index';

/**
 * PDF Generation Utility for AI Analysis Reports
 * 
 * This module exports a function to convert JSON analysis reports into
 * professional A4 PDF documents with automatic page management.
 */

// ============================================================================
// CONSTANTS & CONFIGURATION
// ============================================================================

/** A4 page width in millimeters */
const PAGE_WIDTH_MM = 210;

/** Left margin in millimeters */
const MARGIN_LEFT = 15;

/** Right margin in millimeters */
const MARGIN_RIGHT = 15;

/** Top margin in millimeters */
const MARGIN_TOP = 20;

/**
 * Y-AXIS PAGE BREAK THRESHOLD (280mm)
 * 
 * Mathematical Logic:
 * - A4 page height = 297mm
 * - Bottom margin = 20mm
 * - Available content space = 297mm - 20mm = 277mm
 * - We use 280mm as threshold to trigger page break BEFORE content reaches edge
 * - This ensures minimum 17mm buffer zone at page bottom for safety
 * - Formula: PAGE_HEIGHT_MM - MARGIN_BOTTOM - 1mm padding = 276mm (conservative)
 * - We use 280mm to be slightly more aggressive in page breaks, preventing text cutoff
 */
const Y_POSITION_PAGE_BREAK_THRESHOLD = 280;

/**
 * Content width for text wrapping calculation (in mm)
 * 
 * Mathematical Logic:
 * - Page width = 210mm
 * - Left margin = 15mm
 * - Right margin = 15mm
 * - Available width = 210mm - 15mm - 15mm = 180mm
 * - This width is used with jsPDF's splitTextToSize() for proper text wrapping
 */
const CONTENT_WIDTH_MM = PAGE_WIDTH_MM - MARGIN_LEFT - MARGIN_RIGHT;

/** Font sizes used in PDF (in points) */
const FONT_SIZES = {
  title: 16,
  sectionHeader: 12,
  bodyText: 10,
  date: 10,
  score: 12,
};

/** Line height multiplier for spacing calculations (in mm) */
const LINE_HEIGHT_MULTIPLIER = 0.5;

/** Vertical spacing between sections (in mm) */
const SECTION_SPACING = 6;

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

/**
 * Calculates approximate line height in millimeters
 * 
 * Mathematical Logic:
 * - Font size in points needs to be converted to mm
 * - jsPDF uses points internally (1 point ≈ 0.353mm)
 * - Line height = font_size_points × 0.353 × LINE_HEIGHT_MULTIPLIER
 * - For 10pt font: 10 × 0.353 × 0.5 ≈ 1.765mm
 * - Used for Y-position calculations during text layout
 * 
 * @param fontSizePoints - Font size in points
 * @returns Line height in millimeters
 */
function getLineHeightMm(fontSizePoints: number): number {
  const PT_TO_MM = 0.353;
  return fontSizePoints * PT_TO_MM * LINE_HEIGHT_MULTIPLIER;
}

/**
 * Retrieves the current date in YYYY-MM-DD format
 * 
 * @returns Current date as formatted string
 */
function getCurrentDateString(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * Checks if a new page is needed and adds one if necessary
 * 
 * Mathematical Logic:
 * - Before writing content at current Y position, check if it exceeds threshold
 * - If currentY ≥ 280mm: content might overflow page
 * - Add new page and reset Y to top margin (20mm)
 * - This ensures consistent spacing and prevents text cutoff at page boundaries
 * 
 * @param doc - jsPDF document instance
 * @param currentY - Current Y position in millimeters
 * @param requiredHeight - Height of content about to be added (in mm)
 * @returns Updated Y position (either current or reset to MARGIN_TOP)
 */
function checkAndAddPageBreak(
  doc: jsPDF,
  currentY: number,
  requiredHeight: number = 5
): number {
  if (currentY + requiredHeight > Y_POSITION_PAGE_BREAK_THRESHOLD) {
    doc.addPage();
    return MARGIN_TOP;
  }
  return currentY;
}

// ============================================================================
// MAIN PDF EXPORT FUNCTION
// ============================================================================

const formatSevenCsEvaluation = (evaluation: unknown): string => {
  if (typeof evaluation === 'string') {
    return evaluation;
  }
  if (evaluation === null || typeof evaluation !== 'object') {
    return '';
  }

  const parts: string[] = [];
  if ('status' in evaluation && typeof evaluation.status === 'string' && evaluation.status) {
    parts.push(evaluation.status);
  }
  if ('feedback' in evaluation && typeof evaluation.feedback === 'string' && evaluation.feedback) {
    parts.push(evaluation.feedback);
  }
  return parts.join(': ');
};

/**
 * Generates and downloads a professional PDF report from analysis data
 * 
 * @param reportData - The AnalysisReport object containing analysis results
 * @param title - Document title to appear at top of PDF
 * 
 * Process Flow:
 * 1. Initialize jsPDF with A4 format
 * 2. Add header section (title, date, overall score)
 * 3. Add executive summary section
 * 4. Add category scores section
 * 5. Add 7 Cs evaluation section
 * 6. Add key takeaways section
 * 7. Save file with dynamic filename "{title}-{YYYY-MM-DD}.pdf"
 */
export function downloadReportPdf(
  reportData: AnalysisReport,
  title: string
): void {
  // Initialize PDF document with A4 format and millimeter units
  const doc = new jsPDF({
    orientation: 'portrait',
    unit: 'mm',
    format: 'a4',
  });

  // Set default font for entire document
  doc.setFont('Helvetica');

  let yPosition = MARGIN_TOP;

  // ========================================================================
  // SECTION 1: HEADER
  // ========================================================================

  // Add document title (16pt, Bold)
  doc.setFontSize(FONT_SIZES.title);
  doc.setFont('Helvetica', 'bold');
  doc.text(title, MARGIN_LEFT, yPosition);
  yPosition += getLineHeightMm(FONT_SIZES.title) + 2;

  // Add generation date (10pt, Regular)
  doc.setFontSize(FONT_SIZES.date);
  doc.setFont('Helvetica', 'normal');
  const generationDate = getCurrentDateString();
  doc.text(`Generated: ${generationDate}`, MARGIN_LEFT, yPosition);
  yPosition += getLineHeightMm(FONT_SIZES.date) + 3;

  // Add overall score (12pt, Bold)
  doc.setFontSize(FONT_SIZES.score);
  doc.setFont('Helvetica', 'bold');
  const scorePercentage = Math.round(reportData.overall_score * 10) / 10;
  doc.text(`Overall Score: ${scorePercentage}/100`, MARGIN_LEFT, yPosition);
  yPosition += getLineHeightMm(FONT_SIZES.score) + SECTION_SPACING;

  // Check if page break needed before executive summary
  yPosition = checkAndAddPageBreak(doc, yPosition, 15);

  // ========================================================================
  // SECTION 2: EXECUTIVE SUMMARY
  // ========================================================================

  doc.setFontSize(FONT_SIZES.sectionHeader);
  doc.setFont('Helvetica', 'bold');
  doc.text('Executive Summary', MARGIN_LEFT, yPosition);
  yPosition += getLineHeightMm(FONT_SIZES.sectionHeader) + 2;

  // Generate executive summary text from report data
  const executiveSummary = `This report provides a comprehensive analysis of "${reportData.document_name}". The document achieved an overall score of ${scorePercentage}/100, demonstrating ${
    scorePercentage >= 80
      ? 'excellent quality across all evaluated criteria'
      : scorePercentage >= 60
        ? 'good quality with opportunities for improvement'
        : 'areas requiring significant revision'
  }. The analysis evaluates multiple dimensions including document structure, clarity, completeness, and adherence to professional communication standards.`;

  /**
   * TEXT WRAPPING ALGORITHM
   * 
   * Mathematical Logic for splitTextToSize():
   * - Input: text string, maximum width (180mm), font configuration
   * - Algorithm breaks text into lines that fit within 180mm at 10pt Helvetica
   * - Each character width is calculated based on font metrics
   * - Helvetica 10pt average character width ≈ 2.3mm
   * - For 180mm width: max ~78 characters per line at 10pt
   * - splitTextToSize() returns array of text lines
   * - We iterate through lines and update Y position for each
   * - Y increment per line ≈ 3.5mm (10pt font at 0.5 line height multiplier)
   * - This ensures text wraps cleanly within content boundaries
   */
  doc.setFontSize(FONT_SIZES.bodyText);
  doc.setFont('Helvetica', 'normal');
  const summaryLines = doc.splitTextToSize(
    executiveSummary,
    CONTENT_WIDTH_MM
  );

  for (const line of summaryLines) {
    yPosition = checkAndAddPageBreak(doc, yPosition, 4);
    doc.text(line, MARGIN_LEFT, yPosition);
    yPosition += getLineHeightMm(FONT_SIZES.bodyText) + 1;
  }

  yPosition += SECTION_SPACING;

  // Check if page break needed before category scores
  yPosition = checkAndAddPageBreak(doc, yPosition, 20);

  // ========================================================================
  // SECTION 3: CATEGORY SCORES
  // ========================================================================

  doc.setFontSize(FONT_SIZES.sectionHeader);
  doc.setFont('Helvetica', 'bold');
  doc.text('Category Scores', MARGIN_LEFT, yPosition);
  yPosition += getLineHeightMm(FONT_SIZES.sectionHeader) + 3;

  // Format and display each category score
  const categoryEntries = Object.entries(reportData.category_scores);
  doc.setFontSize(FONT_SIZES.bodyText);
  doc.setFont('Helvetica', 'normal');

  for (const [category, score] of categoryEntries) {
    yPosition = checkAndAddPageBreak(doc, yPosition, 4);
    const scoreVal = score !== undefined ? score : 0;
    const categoryLine = `${category}: ${Math.round(scoreVal * 10) / 10}/100`;
    doc.text(categoryLine, MARGIN_LEFT + 5, yPosition);
    yPosition += getLineHeightMm(FONT_SIZES.bodyText) + 2;
  }

  yPosition += SECTION_SPACING;

  // Check if page break needed before 7 Cs evaluation
  yPosition = checkAndAddPageBreak(doc, yPosition, 25);

  // ========================================================================
  // SECTION 4: 7 Cs EVALUATION
  // ========================================================================

  doc.setFontSize(FONT_SIZES.sectionHeader);
  doc.setFont('Helvetica', 'bold');
  doc.text('7 Cs Communication Evaluation', MARGIN_LEFT, yPosition);
  yPosition += getLineHeightMm(FONT_SIZES.sectionHeader) + 3;

  doc.setFontSize(FONT_SIZES.bodyText);
  doc.setFont('Helvetica', 'normal');

  const evaluations = reportData.seven_cs_evaluation;
  const hasCanonicalCoherentEvaluation = Object.prototype.hasOwnProperty.call(evaluations, 'Coherent');
  const sevenCsEntries = Object.entries(evaluations)
    .filter(([criterion]) => criterion !== 'Consistent' || !hasCanonicalCoherentEvaluation)
    .map(([criterion, evaluation]) => ({
      criterion: criterion === 'Consistent' ? 'Coherent' : criterion,
      evaluation,
    }));

  for (const { criterion, evaluation } of sevenCsEntries) {
    yPosition = checkAndAddPageBreak(doc, yPosition, 8);

    // Bold criterion name
    doc.setFont('Helvetica', 'bold');
    doc.text(`${criterion}:`, MARGIN_LEFT + 5, yPosition);
    yPosition += getLineHeightMm(FONT_SIZES.bodyText) + 1;

    // Regular evaluation text with wrapping
    doc.setFont('Helvetica', 'normal');
    const evaluationLines = doc.splitTextToSize(
      formatSevenCsEvaluation(evaluation),
      CONTENT_WIDTH_MM - 5
    );

    for (const line of evaluationLines) {
      yPosition = checkAndAddPageBreak(doc, yPosition, 4);
      doc.text(line, MARGIN_LEFT + 10, yPosition);
      yPosition += getLineHeightMm(FONT_SIZES.bodyText) + 0.5;
    }

    yPosition += 2;
  }

  yPosition += SECTION_SPACING;

  // Check if page break needed before key takeaways
  yPosition = checkAndAddPageBreak(doc, yPosition, 15);

  // ========================================================================
  // SECTION 5: KEY TAKEAWAYS
  // ========================================================================

  doc.setFontSize(FONT_SIZES.sectionHeader);
  doc.setFont('Helvetica', 'bold');
  doc.text('Key Takeaways & Recommendations', MARGIN_LEFT, yPosition);
  yPosition += getLineHeightMm(FONT_SIZES.sectionHeader) + 3;

  doc.setFontSize(FONT_SIZES.bodyText);
  doc.setFont('Helvetica', 'normal');

  /**
   * RECOMMENDATION LIST RENDERING
   * 
   * Mathematical Logic:
   * - Each recommendation bullet point includes:
   *   - Bullet character (•)
   *   - Recommendation text
   * - Text wrapping applied per recommendation
   * - Y position incremented based on number of wrapped lines
   * - Page break checked before each recommendation
   * - Ensures all content remains within page boundaries
   */
  for (let i = 0; i < reportData.recommendations.length; i++) {
    yPosition = checkAndAddPageBreak(doc, yPosition, 6);

    const recommendation = reportData.recommendations[i];
    const bulletText = `• ${recommendation}`;
    const wrappedLines = doc.splitTextToSize(
      bulletText,
      CONTENT_WIDTH_MM - 10
    );

    for (const line of wrappedLines) {
      yPosition = checkAndAddPageBreak(doc, yPosition, 4);
      doc.text(line, MARGIN_LEFT + 5, yPosition);
      yPosition += getLineHeightMm(FONT_SIZES.bodyText) + 1;
    }

    yPosition += 2;
  }

  // ========================================================================
  // SAVE PDF
  // ========================================================================

  /**
   * FILENAME GENERATION
   * 
   * Format: "{title}-{YYYY-MM-DD}.pdf"
   * Example: "Presentation Analysis-2024-12-15.pdf"
   * 
   * Process:
   * 1. Get current date in YYYY-MM-DD format
   * 2. Sanitize title (remove special characters that break filenames)
   * 3. Combine with dash separator
   * 4. Call doc.save() to trigger browser download
   */
  const filename = `${title.replace(/[/\\:*?"<>|]/g, '')}-${generationDate}.pdf`;
  doc.save(filename);
}

/**
 * Alternative: Generate PDF as Blob (for custom handling or upload)
 * Uncomment to use instead of downloadReportPdf for server upload scenarios
 */
export function generateReportPdfBlob(
  _reportData: AnalysisReport,
  _title: string
): Blob {
  // Create document using same logic as downloadReportPdf
  const doc = new jsPDF({
    orientation: 'portrait',
    unit: 'mm',
    format: 'a4',
  });

  // [Same content generation logic as above would be reused here]
  // This is a placeholder showing the alternative approach

  return doc.output('blob');
}
