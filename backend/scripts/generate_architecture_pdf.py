"""
generate_architecture_pdf.py
============================
A STANDALONE UTILITY, not part of the application.

It renders a printable architecture document to PDF using reportlab. Nothing
in the API or the worker imports it, and it is never executed automatically.

It previously lived in `app/tasks/`, which was misleading: everything else in
that package is a Celery background job, whereas this is a one-off script you
run by hand. It was moved here so the tasks package contains only real tasks.

TO RUN IT:
    pip install reportlab        # deliberately NOT in requirements.txt, since
                                 # the application itself does not need it
    python scripts/generate_architecture_pdf.py
"""

import sys
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas to dynamically compute and display 'Page X of Y' in the footer.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        
        # Color definitions
        primary_color = colors.HexColor("#1E3A8A")  # Navy Blue
        text_color = colors.HexColor("#6B7280")     # Medium Gray
        
        # Page size
        width, height = letter
        
        # Draw header (on later pages, skip first page)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(primary_color)
            self.drawString(54, height - 36, "DOCMINDS")
            
            self.setFont("Helvetica", 8)
            self.setFillColor(text_color)
            self.drawRightString(width - 54, height - 36, "System Architecture & Guide")
            
            # Header line
            self.setStrokeColor(colors.HexColor("#E5E7EB"))
            self.setLineWidth(0.75)
            self.line(54, height - 42, width - 54, height - 42)
            
        # Draw footer (all pages)
        self.setStrokeColor(colors.HexColor("#E5E7EB"))
        self.setLineWidth(0.75)
        self.line(54, 50, width - 54, 50)
        
        self.setFont("Helvetica", 8)
        self.setFillColor(text_color)
        self.drawString(54, 38, "Confidential - Internal Architecture Reference Guide")
        self.drawRightString(width - 54, 38, f"Page {self._pageNumber} of {page_count}")
        
        self.restoreState()

def create_architecture_pdf(output_path):
    # Page setup - letter is 8.5 x 11 inches (612 x 792 points)
    # Margins: 0.75 in (54 pt) all sides
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=72
    )
    
    # Styles Setup
    styles = getSampleStyleSheet()
    
    # Color palette
    navy_blue = colors.HexColor("#1E3A8A")
    charcoal = colors.HexColor("#1F2937")
    bg_light = colors.HexColor("#F9FAFB")
    border_gray = colors.HexColor("#E5E7EB")
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=26,
        leading=32,
        textColor=navy_blue,
        spaceAfter=10
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#4B5563"),
        spaceAfter=30
    )
    
    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=navy_blue,
        spaceBefore=15,
        spaceAfter=12,
        keepWithNext=True
    )
    
    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#111827"),
        spaceBefore=10,
        spaceAfter=6,
        keepWithNext=True
    )
    
    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14.5,
        textColor=charcoal,
        spaceAfter=8
    )
    
    
    
    story = []
    
    # ------------------ TITLE SECTION ------------------
    story.append(Spacer(1, 40))
    story.append(Paragraph("DocMinds", title_style))
    story.append(Paragraph("System Architecture & Processing Workflow: A Guide for Business Leaders", subtitle_style))
    
    # Thin divider line
    d = Table([['']], colWidths=[doc.width], rowHeights=[2])
    d.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), navy_blue),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(d)
    story.append(Spacer(1, 20))
    
    # ------------------ EXECUTIVE SUMMARY (CALLOUT BOX) ------------------
    summary_text = (
        "<b>Executive Summary:</b> Documents are the lifeblood of modern enterprise, but manual data extraction "
        "and reading is slow, expensive, and error-prone. The <i>DocMinds</i> is a "
        "state-of-the-art software engine built to automatically ingest, read, interpret, and securely structure unstructured files "
        "(such as PDFs, scans, Word files, Excel files, and emails). By converting plain files into highly searchable and "
        "AI-ready information segments (vectors), it empowers organizations to instantly search, query, and unlock insights "
        "from their internal knowledge base."
    )
    callout_p = Paragraph(summary_text, ParagraphStyle('SummaryText', parent=body_style, fontSize=10, leading=15, textColor=colors.HexColor("#1E3A8A")))
    summary_box = Table([[callout_p]], colWidths=[doc.width])
    summary_box.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EFF6FF")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#BFDBFE")),
        ('TOPPADDING', (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LEFTPADDING', (0,0), (-1,-1), 15),
        ('RIGHTPADDING', (0,0), (-1,-1), 15),
    ]))
    story.append(summary_box)
    story.append(Spacer(1, 20))
    
    # ------------------ SYSTEM HIGHLIGHTS (THE PROBLEM & SOLUTION) ------------------
    story.append(Paragraph("The Core Challenge & Our Solution", h1_style))
    story.append(Paragraph(
        "For non-technical readers, standard computers view files like PDFs or spreadsheets simply as pixels on a screen "
        "or massive unstructured blocks of text. Traditional search engines can search for exact words, but they don't "
        "<i>understand context</i>. For instance, searching for 'agreements' might miss a document titled 'Service Level SLA'.",
        body_style
    ))
    story.append(Paragraph(
        "This platform solves this by running an intelligent, assembly-line-like ingestion pipeline. Whenever an organization "
        "member uploads a document, the system does not just save the file. It opens it, reads scanned images using artificial "
        "sight (OCR), cuts the document into digestible pieces, and translates those pieces into a sequence of mathematical numbers "
        "(called 'embeddings') that capture the deep conceptual meaning of the sentences. This allows AI models to later locate the "
        "exact answer to questions in seconds.",
        body_style
    ))
    
    story.append(Spacer(1, 10))
    story.append(PageBreak()) # Clean page breaks make PDFs look professional

    # ------------------ HIGH-LEVEL ARCHITECTURE MAP ------------------
    story.append(Paragraph("System Architecture Overview", h1_style))
    story.append(Paragraph(
        "To ensure the system remains lighting-fast even when processing millions of pages, the architecture is divided into "
        "three primary components, operating like a high-end restaurant:",
        body_style
    ))
    
    # Grid of components
    comp_data = [
        [
            Paragraph("<b>1. The Receptionist (FastAPI Web Server)</b>", h2_style),
            Paragraph("<b>2. The Kitchen Crew (Celery & Redis)</b>", h2_style),
            Paragraph("<b>3. The Archive Vault (PostgreSQL & pgvector)</b>", h2_style)
        ],
        [
            Paragraph(
                "This handles the user interface requests. It manages security (who is logged in, who owns what project), "
                "accepts uploads, and displays results. Because it needs to respond instantly, it never performs heavy document reading itself. "
                "Instead, it registers the file and immediately hands the hard work to the kitchen crew.",
                body_style
            ),
            Paragraph(
                "Document processing is heavy, slow work. To prevent the server from freezing, this system uses <i>Celery</i>. "
                "It is a background worker network. <i>Redis</i> acts as the messaging conveyor belt. The server places a processing task on "
                "the belt, and Celery workers pick it up and process it silently in the background.",
                body_style
            ),
            Paragraph(
                "Once text is extracted and analyzed, it must be stored securely. We use <i>PostgreSQL</i>, an enterprise-grade database. "
                "Specifically, we equip it with a module called <i>pgvector</i>. This acts like a spatial map of concepts, storing coordinates (vectors) "
                "for sentences so we can find conceptually similar texts instantly.",
                body_style
            )
        ]
    ]
    
    comp_table = Table(comp_data, colWidths=[doc.width/3.0 - 10, doc.width/3.0 - 10, doc.width/3.0 - 10])
    comp_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BACKGROUND', (0,0), (-1,-1), bg_light),
        ('BOX', (0,0), (-1,-1), 0.5, border_gray),
        ('INNERGRID', (0,0), (-1,-1), 0.5, border_gray),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(comp_table)
    story.append(Spacer(1, 20))
    
    # High level diagram representation using simple table
    story.append(Paragraph("Visual Workflow of Information Flow", h2_style))
    
    diag_data = [
        [
            Paragraph("<b>User Uploads File</b><br/><font color='#6B7280'>PDF, Word, Excel, Email, ZIP</font>", body_style),
            Paragraph("➔", ParagraphStyle('Arrow', parent=body_style, alignment=1, fontSize=16, textColor=navy_blue)),
            Paragraph("<b>FastAPI Web Server</b><br/><font color='#6B7280'>Receives file, saves metadata</font>", body_style),
            Paragraph("➔", ParagraphStyle('Arrow', parent=body_style, alignment=1, fontSize=16, textColor=navy_blue)),
            Paragraph("<b>Redis Broker</b><br/><font color='#6B7280'>Queues task for processing</font>", body_style)
        ],
        [
            Paragraph("<b>PostgreSQL + pgvector</b><br/><font color='#6B7280'>Saves chunks & concept vectors</font>", body_style),
            Paragraph("Selection / Store", ParagraphStyle('ArrowTxt', parent=body_style, alignment=1, fontSize=8, textColor=navy_blue)),
            Paragraph("<b>AI Vector Embeddings</b><br/><font color='#6B7280'>Generates mathematical meaning</font>", body_style),
            Paragraph("⬅", ParagraphStyle('Arrow', parent=body_style, alignment=1, fontSize=16, textColor=navy_blue)),
            Paragraph("<b>Celery Workers</b><br/><font color='#6B7280'>Extracts text, runs OCR, chunks text</font>", body_style)
        ]
    ]
    diag_table = Table(diag_data, colWidths=[120, 30, 140, 30, 120])
    diag_table.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F3F4F6")),
        ('BOX', (0,0), (-1,-1), 1, border_gray),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(diag_table)
    
    story.append(Spacer(1, 10))
    story.append(PageBreak())

    # ------------------ DETAILED INGESTION PIPELINE ------------------
    story.append(Paragraph("Inside the Document Processing Pipeline", h1_style))
    story.append(Paragraph(
        "When the Celery worker handles a document, it routes it through a specialized pipeline with 5 stages. "
        "Here is what happens under the hood in plain terms:",
        body_style
    ))
    
    pipeline_steps = [
        ("1. Security & Integrity Scan", 
         "The system conducts file integrity checks to confirm that the file is not corrupt and simulates a "
         "malware/virus scan. This prevents malicious uploads from contaminating database services."),
        
        ("2. Text & Layout Extraction (Reading)", 
         "This is handled by our <b>Document Extractor</b>. Files can take many shapes: spreadsheets, presentations, "
         "scanned images, text files, emails, or even compressed ZIP folders. The extractor has specific custom readers "
         "for each type. For example: "
         "<br/>• <i>Word Documents:</i> Paragraphs are read chronologically, and cell content is extracted from tables."
         "<br/>• <i>Excel Sheets:</i> Grids are converted into readable tables, omitting completely blank rows."
         "<br/>• <i>Emails (.eml):</i> Headers (Sender, Recipient, Subject, Date) are preserved alongside the email body."),
        
        ("3. Optical Character Recognition (OCR Failsafe)", 
         "Often, documents are scanned images or static PDFs containing no selectable text. If the system detects a PDF "
         "has very low text density, it automatically triggers our <b>OCR Service</b>. "
         "This service supports industry-standard neural sight frameworks like <i>Tesseract</i>, <i>EasyOCR</i>, and <i>PaddleOCR</i>. "
         "It rasterizes pages into crisp, high-resolution images in memory, reads the text within them, and feeds it back into the pipeline."),
        
        ("4. Smart Text Chunking (Slicing)", 
         "Large documents cannot be fed into AI search indices all at once due to memory and context constraints. "
         "Our <b>Chunker Service</b> slices documents into manageable sections using customizable strategies:"
         "<br/>• <i>Fixed-Size Strategy:</i> Splits text into segments containing an exact number of word-tokens, with a minor overlapping margin so context isn't lost at the border."
         "<br/>• <i>Sentence-Aware Strategy:</i> Cuts text at sentence boundaries (periods, exclamation marks) so thoughts are kept whole, rather than splitting sentences in half."
         "<br/>• <i>Markdown Strategy:</i> Slices the document based on headers (# Titles, ## Sections), mapping layout structures naturally."),
        
        ("5. Generating Concept Vectors (Brain Work)", 
         "Finally, we run the chunks through the <b>Embedding Service</b>. Words are converted into high-dimensional numerical "
         "vectors. Two sentences that mean similar things but use entirely different words (e.g., 'Company income increased' vs. "
         "'Acme revenue grew') will generate vectors that are geometrically close in mathematical space. "
         "The system supports leading AI providers like <i>OpenAI</i> (text-embedding-3-small) and local models like <i>HuggingFace</i>, "
         "with a mock generator for offline development.")
    ]
    
    for step_title, step_desc in pipeline_steps:
        story.append(Paragraph(f"<b>{step_title}</b>", h2_style))
        story.append(Paragraph(step_desc, body_style))
        story.append(Spacer(1, 4))
        
    story.append(Spacer(1, 10))
    story.append(PageBreak())

    # ------------------ BUSINESS BENEFITS & DESIGN ADVANTAGES ------------------
    story.append(Paragraph("Key Benefits for Business & Strategy", h1_style))
    story.append(Paragraph(
        "Understanding why these architecture decisions were made is crucial for evaluating its business impact:",
        body_style
    ))
    
    benefits = [
        ("Multi-Format Standardisation", 
         "Instead of using separate systems for emails, spreadsheets, and PDFs, this assistant normalizes all incoming "
         "information into a single standard chunk format. Employees can search across all file formats with a single query."),
        
        ("Intelligent Context Recall", 
         "Because the database is built around pgvector, search is based on <i>meaning</i> rather than keywords. "
         "This saves hundreds of hours of manual document review by returning the exact paragraph answers to complex questions."),
        
        ("Failsafe Robustness", 
         "The code features extensive custom checks and automatic fallbacks. If the system fails to read text directly "
         "from a PDF, it falls back to OCR. If configured AI services are offline, it falls back to local models or mock placeholders "
         "to prevent system crashes, maximizing system uptime."),
        
        ("Asynchronous Efficiency", 
         "Processing large documents takes time. By queueing jobs, the web app feels fast and instantaneous to users. "
         "They upload their files, see a 'Processing' status, and can perform other tasks while the work is done in the background."),
        
        ("Enterprise Boundaries", 
         "The system segregates documents cleanly into 'Projects' under 'Organizations', which prevents cross-tenant data leaks. "
         "Audit logs keep track of every action, fulfilling enterprise compliance and security standards.")
    ]
    
    benefit_data = []
    for title, desc in benefits:
        benefit_data.append([
            Paragraph(f"<b>{title}</b>", h2_style),
            Paragraph(desc, body_style)
        ])
        
    benefit_table = Table(benefit_data, colWidths=[150, doc.width - 150])
    benefit_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LINEBELOW', (0,0), (-1,-2), 0.5, border_gray),
        ('TOPPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(benefit_table)
    story.append(Spacer(1, 20))
    
    # ------------------ FOOTNOTE / CONTACT ------------------
    footnote_text = (
        "<b>Note on Document Access:</b> This PDF was auto-generated to reflect the codebase's architecture dynamically. "
        "To change system settings (such as replacing the OCR engine or adding OpenAI keys), developers can modify the "
        "system environment configuration file (<code>.env</code>) or contact the IT Infrastructure lead."
    )
    footnote_p = Paragraph(footnote_text, ParagraphStyle('FootnoteText', parent=body_style, fontSize=8.5, leading=12, textColor=colors.HexColor("#4B5563")))
    footnote_box = Table([[footnote_p]], colWidths=[doc.width])
    footnote_box.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F9FAFB")),
        ('BOX', (0,0), (-1,-1), 0.5, border_gray),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(footnote_box)
    
    # Build Document
    doc.build(story, canvasmaker=NumberedCanvas)
    print("PDF successfully generated.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_pdf.py <output_path>")
        sys.exit(1)
    
    out_path = sys.argv[1]
    create_architecture_pdf(out_path)
