/**
 * i18n/upload.ts -- the upload zone's strings, English and Hindi.
 * See i18n/index.ts. Keys are prefixed "upload." so areas never collide.
 */

import type { AreaMessages } from "./index";

const messages: AreaMessages = {
  en: {
    "upload.idle": "Drag files here, or click to browse",
    "upload.dragOver": "Drop to upload",
    "upload.uploading": "Uploading...",
    "upload.processing": "Processing...",
    "upload.done": "Uploaded — processing continues in the background",
    "upload.error": "Some files could not be uploaded",
    "upload.formats": "PDF · DOCX · PPTX · XLSX · HTML · EML · CSV · TXT · MD · ZIP · images — many at once",
    "upload.ocr": "Scanned PDFs are detected automatically and sent through OCR.",
    "upload.summary": "{done} of {total} uploaded",
    "upload.failedCount": " · {failed} failed",
    "upload.clearErrors": "Clear errors",
    "upload.list": "Uploads",
    "upload.queued": "queued",
    "upload.rowProcessing": "processing",
    "upload.rowUploaded": "Uploaded",
    "upload.rowFailed": "Failed",
    "upload.rowUploading": "Uploading",
    "upload.progressLabel": "Uploading {name}",
    "upload.zoneLabel": "Upload files",
  },
  hi: {
    "upload.idle": "फ़ाइलें यहाँ खींचें, या ब्राउज़ करने के लिए क्लिक करें",
    "upload.dragOver": "अपलोड करने के लिए छोड़ें",
    "upload.uploading": "अपलोड हो रहा है...",
    "upload.processing": "प्रोसेस हो रहा है...",
    "upload.done": "अपलोड हो गया — प्रोसेसिंग पृष्ठभूमि में जारी है",
    "upload.error": "कुछ फ़ाइलें अपलोड नहीं हो सकीं",
    "upload.formats": "PDF · DOCX · PPTX · XLSX · HTML · EML · CSV · TXT · MD · ZIP · चित्र — एक साथ कई",
    "upload.ocr": "स्कैन की गई PDF अपने-आप पहचानी जाती हैं और OCR से गुज़रती हैं।",
    "upload.summary": "{total} में से {done} अपलोड हुईं",
    "upload.failedCount": " · {failed} विफल",
    "upload.clearErrors": "त्रुटियाँ हटाएँ",
    "upload.list": "अपलोड",
    "upload.queued": "कतार में",
    "upload.rowProcessing": "प्रोसेसिंग",
    "upload.rowUploaded": "अपलोड हुआ",
    "upload.rowFailed": "विफल",
    "upload.rowUploading": "अपलोड हो रहा है",
    "upload.progressLabel": "{name} अपलोड हो रहा है",
    "upload.zoneLabel": "फ़ाइलें अपलोड करें",
  },
};

export default messages;
