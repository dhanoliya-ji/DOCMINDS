/**
 * i18n/dashboard.ts -- the dashboard, the first-run help (Onboarding.tsx),
 * project cards and the email-confirmation banner, English and Hindi.
 * See i18n/index.ts.
 *
 * Strings that wrap markup (a link, <code>, <strong>) use `{name}` slots
 * filled with elements by `rich()` in i18n/index.ts, so each language can
 * put the link where its grammar wants it.
 */

import type { AreaMessages } from "./index";

const messages: AreaMessages = {
  en: {
    "dashboard.title": "Projects",
    "dashboard.subtitle": "Each project is its own document collection and search boundary.",
    "dashboard.newProject": "New project",
    "dashboard.demo.title": "Running in demo mode",
    "dashboard.demo.body":
      "Upload, OCR, chunking and semantic search all work fully. Chat returns the best matching passage directly instead of an AI-written summary. To enable AI answers, add a free key from {link} to {file} as {variable}.",
    "dashboard.loadError": "Could not load your projects.",
    "dashboard.createError": "Could not create the project.",
    "dashboard.loading": "Loading projects",
    "dashboard.empty.title": "No projects yet",
    "dashboard.empty.editor":
      "Create your first project, then upload documents into it to start asking questions.",
    "dashboard.empty.viewer": "Your organization has no projects yet. Ask an editor or admin to create one.",
    "dashboard.empty.examples": "Start from an example",
    "dashboard.sample.1": "HR Policies",
    "dashboard.sample.2": "Q3 Contracts",
    "dashboard.sample.3": "Product Docs",
    "dashboard.sample.4": "Research Papers",
    "dashboard.createProject": "Create a project",
    "dashboard.dialog.close": "Close",
    "dashboard.dialog.name": "Project name",
    "dashboard.dialog.placeholder": "e.g. HR Policies, Q3 Contracts",
    "dashboard.dialog.cancel": "Cancel",
    "dashboard.dialog.create": "Create",
    "dashboard.tour.1.title": "Projects keep answers on topic",
    "dashboard.tour.1.body":
      "Each project is its own document collection. Questions asked inside one only search the files in it.",
    "dashboard.tour.2.title": "Upload, then wait a moment",
    "dashboard.tour.2.body":
      "Open a project and drop in PDFs, Office files, images or text. Processing runs in the background.",
    "dashboard.tour.3.title": "Search or Ask AI",
    "dashboard.tour.3.body":
      "Once a document shows Ready, search it semantically or ask questions and get answers with page citations.",

    "onboarding.dismiss": "Dismiss tour",
    "onboarding.step": "Quick tour · step {step} of {total}",
    "onboarding.skip": "Skip",
    "onboarding.gotIt": "Got it",
    "onboarding.next": "Next",

    "projectCard.created": "Created {date}",
    "projectCard.open": "Open workspace: documents, search and Ask AI",

    "verify.region": "Email confirmation",
    "verify.title": "Please confirm your email address",
    "verify.body": "We sent a link to {email}. Click it to confirm this address.",
    "verify.sent": "A new link is on its way to {email}.",
    "verify.error": "Could not send the link. Please try again.",
    "verify.resend": "Resend link",
  },
  hi: {
    "dashboard.title": "प्रोजेक्ट",
    "dashboard.subtitle": "हर प्रोजेक्ट दस्तावेज़ों का अपना संग्रह और अपनी खोज-सीमा है।",
    "dashboard.newProject": "नया प्रोजेक्ट",
    "dashboard.demo.title": "डेमो मोड में चल रहा है",
    "dashboard.demo.body":
      "अपलोड, OCR, चंकिंग और सिमेंटिक खोज पूरी तरह काम करते हैं। चैट AI-लिखित सारांश की जगह सबसे मिलता-जुलता अंश सीधे लौटाती है। AI उत्तर चालू करने के लिए {link} से एक मुफ़्त कुंजी लेकर {file} में {variable} के रूप में जोड़ें।",
    "dashboard.loadError": "आपके प्रोजेक्ट लोड नहीं हो सके।",
    "dashboard.createError": "प्रोजेक्ट नहीं बनाया जा सका।",
    "dashboard.loading": "प्रोजेक्ट लोड हो रहे हैं",
    "dashboard.empty.title": "अभी कोई प्रोजेक्ट नहीं",
    "dashboard.empty.editor": "अपना पहला प्रोजेक्ट बनाएँ, फिर सवाल पूछना शुरू करने के लिए उसमें दस्तावेज़ अपलोड करें।",
    "dashboard.empty.viewer": "आपके संगठन में अभी कोई प्रोजेक्ट नहीं है। किसी एडिटर या एडमिन से एक बनाने को कहें।",
    "dashboard.empty.examples": "किसी उदाहरण से शुरू करें",
    "dashboard.sample.1": "HR नीतियाँ",
    "dashboard.sample.2": "Q3 अनुबंध",
    "dashboard.sample.3": "प्रोडक्ट दस्तावेज़",
    "dashboard.sample.4": "शोध पत्र",
    "dashboard.createProject": "प्रोजेक्ट बनाएँ",
    "dashboard.dialog.close": "बंद करें",
    "dashboard.dialog.name": "प्रोजेक्ट का नाम",
    "dashboard.dialog.placeholder": "जैसे HR नीतियाँ, Q3 अनुबंध",
    "dashboard.dialog.cancel": "रद्द करें",
    "dashboard.dialog.create": "बनाएँ",
    "dashboard.tour.1.title": "प्रोजेक्ट उत्तरों को विषय पर रखते हैं",
    "dashboard.tour.1.body":
      "हर प्रोजेक्ट दस्तावेज़ों का अपना संग्रह है। उसके भीतर पूछे गए सवाल केवल उसी की फ़ाइलों में खोजते हैं।",
    "dashboard.tour.2.title": "अपलोड करें, फिर थोड़ा रुकें",
    "dashboard.tour.2.body":
      "कोई प्रोजेक्ट खोलें और PDF, Office फ़ाइलें, चित्र या टेक्स्ट डालें। प्रोसेसिंग बैकग्राउंड में चलती है।",
    "dashboard.tour.3.title": "खोजें या AI से पूछें",
    "dashboard.tour.3.body":
      "जब दस्तावेज़ ‘तैयार’ दिखाए, उसे अर्थ से खोजें या सवाल पूछें और पेज-संदर्भ के साथ उत्तर पाएँ।",

    "onboarding.dismiss": "टूर बंद करें",
    "onboarding.step": "छोटा टूर · चरण {step} / {total}",
    "onboarding.skip": "छोड़ें",
    "onboarding.gotIt": "समझ गया",
    "onboarding.next": "आगे",

    "projectCard.created": "{date} को बनाया गया",
    "projectCard.open": "वर्कस्पेस खोलें: दस्तावेज़, खोज और AI से पूछें",

    "verify.region": "ईमेल पुष्टि",
    "verify.title": "कृपया अपने ईमेल पते की पुष्टि करें",
    "verify.body": "हमने {email} पर एक लिंक भेजा है। इस पते की पुष्टि के लिए उस पर क्लिक करें।",
    "verify.sent": "नया लिंक {email} पर भेजा जा रहा है।",
    "verify.error": "लिंक नहीं भेजा जा सका। कृपया फिर से कोशिश करें।",
    "verify.resend": "लिंक फिर भेजें",
  },
};

export default messages;
