import { jsPDF } from 'jspdf';

export interface ProgressReportPDFData {
  title: string;
  documentName: string;
  v1Score: number | string;
  v2Score: number | string;
  gain: number | string;
  categoryScores: Array<{ name: string; v1: number | string; v2: number | string }>;
  synthesis: string;
  improvements: string[];
  remaining: string[];
  additionalMetrics?: Array<{ label: string; v1: string; v2: string; change?: string }>;
}

export const downloadProgressReportPDF = (data: ProgressReportPDFData, fileName: string) => {
  const doc = new jsPDF();
  
  // Page limits and geometry
  const marginX = 20;
  let currentY = 20;
  const pageHeight = 297;
  const pageWidth = 210;
  const contentWidth = pageWidth - (2 * marginX); // 170mm

  // Helper to add wrapped body text
  const addText = (text: string, fontSize = 10, isBold = false, color = [51, 65, 85], spacing = 7): void => {
    doc.setFont("helvetica", isBold ? "bold" : "normal");
    doc.setFontSize(fontSize);
    doc.setTextColor(color[0], color[1], color[2]);
    
    const lines: string[] = doc.splitTextToSize(text, contentWidth);
    for (const line of lines) {
      if (currentY > 275) {
        doc.addPage();
        currentY = 20;
      }
      doc.text(line, marginX, currentY);
      currentY += spacing;
    }
  };

  // Helper to draw horizontal line
  const addLine = (color = [226, 232, 240], thickness = 0.5, spacingBefore = 2, spacingAfter = 6): void => {
    currentY += spacingBefore;
    if (currentY > 275) {
      doc.addPage();
      currentY = 20;
    }
    doc.setDrawColor(color[0], color[1], color[2]);
    doc.setLineWidth(thickness);
    doc.line(marginX, currentY, pageWidth - marginX, currentY);
    currentY += spacingAfter;
  };

  // Helper to draw a bullet point list
  const addList = (items: string[], fontSize = 10, bullet = "- "): void => {
    items.forEach(item => {
      doc.setFont("helvetica", "normal");
      doc.setFontSize(fontSize);
      doc.setTextColor(71, 85, 105);
      
      const fullText = bullet + item;
      const lines: string[] = doc.splitTextToSize(fullText, contentWidth);
      
      lines.forEach((line, idx) => {
        if (currentY > 275) {
          doc.addPage();
          currentY = 20;
        }
        // Indent subsequent lines of a wrapped bullet point
        const indentX = idx === 0 ? marginX : marginX + 4;
        doc.text(line, indentX, currentY);
        currentY += 6;
      });
    });
    currentY += 2; // extra padding after list
  };

  // Helper to draw section header
  const addSectionHeader = (sectTitle: string): void => {
    currentY += 4;
    addText(sectTitle, 12, true, [79, 70, 229], 8); // Indigo
    addLine([99, 102, 241], 0.8, 1, 6); // Violet line
  };

  // 1. HEADER TITLE
  addText(data.title, 18, true, [15, 23, 42], 12); // Title (Slate 900)
  
  // 2. METADATA
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(100, 116, 139);
  doc.text(`Practiced Topic/File: ${data.documentName}`, marginX, currentY);
  doc.text(`Generated on: ${new Date().toLocaleDateString()}`, pageWidth - marginX - 50, currentY);
  currentY += 8;

  addLine([203, 213, 225], 1, 2, 8); // Header divider

  // 3. OVERALL SCORE OVERVIEW
  addText("1. Score Progression Summary", 11, true, [15, 23, 42], 8);
  currentY += 2;

  // Draw container box
  doc.setDrawColor(241, 245, 249);
  doc.setFillColor(248, 250, 252);
  doc.rect(marginX, currentY, contentWidth, 25, "F");
  
  // Text inside box
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.setTextColor(71, 85, 105);
  doc.text("Version 1 (Baseline)", marginX + 10, currentY + 8);
  doc.text("Version 2 (Revision)", marginX + 65, currentY + 8);
  doc.text("Overall Progress Gain", marginX + 120, currentY + 8);
  
  doc.setFontSize(14);
  doc.setTextColor(79, 70, 229); // Violet
  doc.text(typeof data.v1Score === 'number' ? `${data.v1Score} / 100` : data.v1Score, marginX + 10, currentY + 18);
  doc.setTextColor(16, 185, 129); // Emerald V2
  doc.text(typeof data.v2Score === 'number' ? `${data.v2Score} / 100` : data.v2Score, marginX + 65, currentY + 18);
  doc.text(typeof data.gain === 'number' ? `+${data.gain} Points` : data.gain, marginX + 120, currentY + 18);
  
  currentY += 32;

  // 4. PERFORMANCE DIMENSION SCORES
  addSectionHeader("2. Performance Category Scores");
  
  // Table Header
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.setTextColor(15, 23, 42);
  doc.text("Category / Dimension", marginX + 5, currentY);
  doc.text("V1 Score", marginX + 90, currentY);
  doc.text("V2 Score", marginX + 120, currentY);
  doc.text("Progress", marginX + 150, currentY);
  currentY += 4;
  addLine([148, 163, 184], 0.5, 1, 5); // Table header line
  
  // Rows
  data.categoryScores.forEach(cat => {
    if (currentY > 275) {
      doc.addPage();
      currentY = 20;
    }
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9.5);
    doc.setTextColor(51, 65, 85);
    doc.text(cat.name, marginX + 5, currentY);
    doc.text(typeof cat.v1 === 'number' ? `${cat.v1}%` : cat.v1, marginX + 90, currentY);
    doc.text(typeof cat.v2 === 'number' ? `${cat.v2}%` : cat.v2, marginX + 120, currentY);
    
    const diff = typeof cat.v1 === 'number' && typeof cat.v2 === 'number'
      ? cat.v2 - cat.v1
      : null;
    if (diff !== null && diff > 0) {
      doc.setTextColor(16, 185, 129);
      doc.setFont("helvetica", "bold");
      doc.text(`+${diff}%`, marginX + 150, currentY);
    } else if (diff !== null && diff < 0) {
      doc.setTextColor(239, 68, 68);
      doc.setFont("helvetica", "bold");
      doc.text(`${diff}%`, marginX + 150, currentY);
    } else if (diff !== null) {
      doc.setTextColor(100, 116, 139);
      doc.text("0%", marginX + 150, currentY);
    } else {
      doc.setTextColor(100, 116, 139);
      doc.text("N/A", marginX + 150, currentY);
    }
    
    currentY += 7;
  });
  currentY += 4;

  // 5. ADDITIONAL DELIVERY METRICS (Speech / Live Video posture data)
  if (data.additionalMetrics && data.additionalMetrics.length > 0) {
    addSectionHeader("3. Visual & Vocal Delivery Metrics");
    
    // Table Header
    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.setTextColor(15, 23, 42);
    doc.text("Metric Label", marginX + 5, currentY);
    doc.text("Version 1", marginX + 80, currentY);
    doc.text("Version 2", marginX + 115, currentY);
    doc.text("Difference", marginX + 150, currentY);
    currentY += 4;
    addLine([148, 163, 184], 0.5, 1, 5); // divider
    
    // Rows
    data.additionalMetrics.forEach(metric => {
      if (currentY > 275) {
        doc.addPage();
        currentY = 20;
      }
      doc.setFont("helvetica", "normal");
      doc.setFontSize(9.5);
      doc.setTextColor(51, 65, 85);
      doc.text(metric.label, marginX + 5, currentY);
      doc.text(metric.v1, marginX + 80, currentY);
      doc.text(metric.v2, marginX + 115, currentY);
      
      if (metric.change) {
        const hasPos = metric.change.includes('+');
        const hasNeg = metric.change.includes('-');
        if (hasPos) {
          doc.setTextColor(16, 185, 129); // Green
        } else if (hasNeg) {
          doc.setTextColor(239, 68, 68); // Red
        } else {
          doc.setTextColor(100, 116, 139); // Gray
        }
        doc.setFont("helvetica", "bold");
        doc.text(metric.change, marginX + 150, currentY);
      }
      currentY += 7;
    });
    currentY += 4;
  }

  // 6. AI COACH PROGRESS SYNTHESIS
  const sectionLabel = data.additionalMetrics ? "4. AI Coach Progress Synthesis" : "3. AI Coach Progress Synthesis";
  addSectionHeader(sectionLabel);
  addText(data.synthesis, 9.5, false, [51, 65, 85], 6);
  currentY += 4;

  // 7. ACHIEVEMENTS & REMAINING POLISH AREAS
  const recommendationsLabel = data.additionalMetrics ? "5. Recommendations & Outcomes" : "4. Recommendations & Outcomes";
  addSectionHeader(recommendationsLabel);
  
  addText("Key Improvements Achieved:", 10.5, true, [16, 185, 129], 6);
  if (data.improvements.length > 0) {
    addList(data.improvements, 9.5, "[x] ");
  } else {
    addText("No major improvement metrics reported yet.", 9.5, false, [100, 116, 139], 6);
    currentY += 2;
  }

  addText("Remaining Areas to Polish:", 10.5, true, [245, 158, 11], 6);
  if (data.remaining.length > 0) {
    addList(data.remaining, 9.5, "[ ] ");
  } else {
    addText("Excellent work! No remaining areas to polish.", 9.5, false, [100, 116, 139], 6);
    currentY += 2;
  }

  // Footer: page numbers
  const totalPages = doc.getNumberOfPages();
  for (let i = 1; i <= totalPages; i++) {
    doc.setPage(i);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.setTextColor(148, 163, 184);
    doc.text(`Page ${i} of ${totalPages}`, pageWidth - marginX - 20, pageHeight - 12);
    doc.text("Generated by AI Presentation & Pitch Analyzer", marginX, pageHeight - 12);
  }

  doc.save(fileName);
};
