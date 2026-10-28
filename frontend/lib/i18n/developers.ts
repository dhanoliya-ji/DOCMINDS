// English and Hindi.

import type { AreaMessages } from "./index";

const messages: AreaMessages = {
  en: {
    "dev.title": "DocMinds API",
    "dev.lede":
      "Everything the app does is an HTTP call you can make yourself: create projects, upload documents, search them and ask questions answered with page-level citations.",
    "dev.back": "Home",
    "dev.reference": "Interactive reference",
    "dev.toc": "On this page",
    "dev.baseUrl": "Base URL",

    "dev.keys.title": "1. Create an API key",
    "dev.keys.body":
      "Programs authenticate with an API key instead of a password. Keys belong to an organization and are created by its admins.",
    "dev.keys.step1": "Sign in as an admin and open Settings → API keys.",
    "dev.keys.step2":
      "Name the key after the program that will use it, pick the lowest role it needs and, optionally, an expiry.",
    "dev.keys.step3":
      "Copy the key (it starts with dm_). It is shown once: only a hash is stored, so a lost key is revoked and replaced, never recovered.",
    "dev.keys.role":
      "A request made with a key acts with the lower of the key's role and its owner's current role, so a leaked viewer key can read but never upload or delete.",
    "dev.keys.open": "Open API keys",

    "dev.auth.title": "2. Authenticate",
    "dev.auth.body": "Send the key in the {header} header on every request. Keep it on the server: never ship it in browser code or a mobile app.",
    "dev.auth.bearer":
      "Signed-in users of the web app use a short-lived bearer token from POST /api/v1/auth/login instead; every endpoint below accepts either.",
    "dev.auth.401": "the key is missing, wrong, revoked or expired",
    "dev.auth.403": "the key's role is below what the endpoint needs",
    "dev.auth.404": "the project or document is not in the key's organization (its existence is not revealed)",

    "dev.limits.title": "3. Rate limits and quotas",
    "dev.limits.body":
      "Expensive endpoints are limited per user (an API key counts as its owner). These are the defaults; a deployment can change them.",
    "dev.limits.endpoint": "Endpoint",
    "dev.limits.limit": "Default limit",
    "dev.limits.per": "Counted per",
    "dev.limits.user": "user",
    "dev.limits.ip": "IP address",
    "dev.limits.headers":
      "Limited responses carry {limit} and {remaining}. Over the limit the API answers 429 with {retry}: wait that many seconds, then retry.",
    "dev.limits.quota":
      "Separately, an organization's monthly AI-token quota answers 429 on chat once used up, and its document or storage quota answers 402 on upload.",

    "dev.endpoints.title": "4. Endpoints",
    "dev.endpoints.body":
      "The most used endpoints are below. Every endpoint, with its request and response schema, is in the {link} (OpenAPI 3.1).",
    "dev.endpoints.method": "Method",
    "dev.endpoints.path": "Path",
    "dev.endpoints.purpose": "What it does",
    "dev.ep.me": "The key's owner: email, role, organization",
    "dev.ep.projects": "List projects / create one (editor)",
    "dev.ep.documents": "Documents in a project, with processing status",
    "dev.ep.upload": "Upload files (multipart, editor); answers 202 while processing runs",
    "dev.ep.search": "Hybrid search: matching passages with scores, no AI",
    "dev.ep.sessions": "Start a conversation in a project",
    "dev.ep.ask": "Ask a question; the answer comes with citations",
    "dev.ep.stream": "The same, streamed as Server-Sent Events",
    "dev.ep.health": "Liveness and configuration (no key needed)",

    "dev.examples.title": "5. Examples",
    "dev.examples.body":
      "Set DOCMINDS_API_KEY in your environment, then copy any of these. Each one is complete.",
    "dev.examples.tabs": "Example language",
    "dev.examples.list": "List your projects",
    "dev.examples.upload": "Upload a document",
    "dev.examples.search": "Search a project",
    "dev.examples.ask": "Ask a question",
    "dev.copy": "Copy",
    "dev.copied": "Copied",
    "dev.copyLabel": "Copy the {name} example",

    "dev.errors.title": "Errors",
    "dev.errors.body":
      "Errors are JSON with a detail field: a sentence, or for 422 a list of the fields that failed validation. Every response carries an X-Request-ID; quote it when reporting a problem.",
  },
  hi: {
    "dev.title": "DocMinds API",
    "dev.lede":
      "ऐप जो कुछ भी करता है, वह एक HTTP कॉल है जिसे आप ख़ुद कर सकते हैं: प्रोजेक्ट बनाएँ, दस्तावेज़ अपलोड करें, उन्हें खोजें और पेज-स्तर के संदर्भों के साथ उत्तर पाने वाले सवाल पूछें।",
    "dev.back": "होम",
    "dev.reference": "इंटरैक्टिव संदर्भ",
    "dev.toc": "इस पेज पर",
    "dev.baseUrl": "बेस URL",

    "dev.keys.title": "1. API कुंजी बनाएँ",
    "dev.keys.body":
      "प्रोग्राम पासवर्ड की जगह API कुंजी से प्रमाणित होते हैं। कुंजियाँ किसी संगठन की होती हैं और उसके एडमिन बनाते हैं।",
    "dev.keys.step1": "एडमिन के रूप में साइन इन करें और सेटिंग्स → API कुंजियाँ खोलें।",
    "dev.keys.step2":
      "कुंजी का नाम उस प्रोग्राम पर रखें जो इसे इस्तेमाल करेगा, उसकी ज़रूरत की सबसे निचली भूमिका चुनें और चाहें तो समाप्ति भी।",
    "dev.keys.step3":
      "कुंजी कॉपी करें (यह dm_ से शुरू होती है)। यह एक ही बार दिखती है: केवल उसका हैश रखा जाता है, इसलिए खोई कुंजी रद्द करके बदली जाती है, वापस नहीं मिलती।",
    "dev.keys.role":
      "कुंजी से किया गया अनुरोध कुंजी की भूमिका और उसके मालिक की मौजूदा भूमिका में से निचली भूमिका से चलता है, इसलिए लीक हुई व्यूअर कुंजी पढ़ तो सकती है पर कभी अपलोड या डिलीट नहीं कर सकती।",
    "dev.keys.open": "API कुंजियाँ खोलें",

    "dev.auth.title": "2. प्रमाणीकरण",
    "dev.auth.body":
      "हर अनुरोध में कुंजी {header} हेडर में भेजें। इसे सर्वर पर ही रखें: ब्राउज़र कोड या मोबाइल ऐप में कभी न डालें।",
    "dev.auth.bearer":
      "वेब ऐप के साइन-इन उपयोगकर्ता इसकी जगह POST /api/v1/auth/login से मिला अल्पकालिक बेयरर टोकन इस्तेमाल करते हैं; नीचे का हर एंडपॉइंट दोनों स्वीकार करता है।",
    "dev.auth.401": "कुंजी नहीं है, ग़लत है, रद्द है या समाप्त हो चुकी है",
    "dev.auth.403": "कुंजी की भूमिका एंडपॉइंट की ज़रूरत से नीचे है",
    "dev.auth.404": "प्रोजेक्ट या दस्तावेज़ कुंजी के संगठन में नहीं है (उसका अस्तित्व नहीं बताया जाता)",

    "dev.limits.title": "3. दर-सीमाएँ और कोटा",
    "dev.limits.body":
      "महँगे एंडपॉइंट प्रति उपयोगकर्ता सीमित हैं (API कुंजी अपने मालिक के रूप में गिनी जाती है)। ये डिफ़ॉल्ट हैं; कोई डिप्लॉयमेंट इन्हें बदल सकता है।",
    "dev.limits.endpoint": "एंडपॉइंट",
    "dev.limits.limit": "डिफ़ॉल्ट सीमा",
    "dev.limits.per": "किसके अनुसार गिना जाता है",
    "dev.limits.user": "उपयोगकर्ता",
    "dev.limits.ip": "IP पता",
    "dev.limits.headers":
      "सीमित जवाबों में {limit} और {remaining} होते हैं। सीमा पार होने पर API {retry} के साथ 429 देता है: उतने सेकंड रुकें, फिर दोबारा कोशिश करें।",
    "dev.limits.quota":
      "इसके अलावा, संगठन का मासिक AI-टोकन कोटा ख़त्म होने पर चैट 429 देती है, और दस्तावेज़ या स्टोरेज कोटा भरने पर अपलोड 402 देता है।",

    "dev.endpoints.title": "4. एंडपॉइंट",
    "dev.endpoints.body":
      "सबसे ज़्यादा इस्तेमाल होने वाले एंडपॉइंट नीचे हैं। हर एंडपॉइंट, उसके अनुरोध और जवाब स्कीमा के साथ, {link} (OpenAPI 3.1) में है।",
    "dev.endpoints.method": "मेथड",
    "dev.endpoints.path": "पाथ",
    "dev.endpoints.purpose": "क्या करता है",
    "dev.ep.me": "कुंजी का मालिक: ईमेल, भूमिका, संगठन",
    "dev.ep.projects": "प्रोजेक्ट की सूची / नया बनाएँ (एडिटर)",
    "dev.ep.documents": "किसी प्रोजेक्ट के दस्तावेज़, प्रोसेसिंग स्थिति के साथ",
    "dev.ep.upload": "फ़ाइलें अपलोड करें (मल्टीपार्ट, एडिटर); प्रोसेसिंग चलते समय 202 देता है",
    "dev.ep.search": "हाइब्रिड खोज: स्कोर के साथ मिलते अंश, बिना AI",
    "dev.ep.sessions": "किसी प्रोजेक्ट में बातचीत शुरू करें",
    "dev.ep.ask": "सवाल पूछें; उत्तर संदर्भों के साथ आता है",
    "dev.ep.stream": "वही, Server-Sent Events के रूप में स्ट्रीम",
    "dev.ep.health": "जीवंतता और कॉन्फ़िगरेशन (कुंजी की ज़रूरत नहीं)",

    "dev.examples.title": "5. उदाहरण",
    "dev.examples.body": "अपने एनवायरनमेंट में DOCMINDS_API_KEY सेट करें, फिर इनमें से कोई भी कॉपी करें। हर एक पूरा है।",
    "dev.examples.tabs": "उदाहरण की भाषा",
    "dev.examples.list": "अपने प्रोजेक्ट की सूची",
    "dev.examples.upload": "एक दस्तावेज़ अपलोड करें",
    "dev.examples.search": "किसी प्रोजेक्ट में खोजें",
    "dev.examples.ask": "एक सवाल पूछें",
    "dev.copy": "कॉपी करें",
    "dev.copied": "कॉपी हो गया",
    "dev.copyLabel": "{name} उदाहरण कॉपी करें",

    "dev.errors.title": "त्रुटियाँ",
    "dev.errors.body":
      "त्रुटियाँ detail फ़ील्ड वाला JSON होती हैं: एक वाक्य, या 422 के लिए उन फ़ील्ड की सूची जिनका सत्यापन विफल हुआ। हर जवाब में X-Request-ID होता है; समस्या बताते समय उसका उल्लेख करें।",
  },
};

export default messages;
