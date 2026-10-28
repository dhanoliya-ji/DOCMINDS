// Hindi.

import type { AreaMessages } from "./index";

const messages: AreaMessages = {
  en: {
    "search.placeholder": "Search by meaning, e.g. “how much time off do I get?”",
    "search.button": "Search",
    "search.hint":
      "This matches meaning AND exact words — a paraphrase finds the passage, and so does a policy number or a name.",
    "search.failed": "Search failed.",
    "search.results.one": "{count} result",
    "search.results.other": "{count} results",
    "search.took": "· found in {ms}ms",
    "search.page": "page {page}",
    "search.match": "{percent}% match",
    "search.view": "View",
    "search.viewLabel": "View {filename} in context",
    "search.chunk": "chunk #{index}",
    "search.foundBy": "found by {how}",
    "search.foundByTitle": "Which half of hybrid search found this passage",
    "search.tokens": "{count} tokens",
    "search.match.both": "meaning + words",
    "search.match.vector": "meaning",
    "search.match.keyword": "exact words",
    "search.empty.title": "Nothing matched closely enough",
    "search.empty.body":
      "Every passage scored below the similarity threshold, so nothing is shown rather than presenting a weak match as an answer. Try rephrasing, or check that documents in this project have finished processing.",

    "filter.all": "All documents",
    "filter.many": "{count} documents",
    "filter.title": "Limit to selected documents",
    "filter.listLabel": "Documents to search",
  },
  hi: {
    "search.placeholder": "अर्थ से खोजें, जैसे “मुझे कितनी छुट्टी मिलती है?”",
    "search.button": "खोजें",
    "search.hint":
      "यह अर्थ और सटीक शब्द दोनों से मिलान करता है — दूसरे शब्दों में कही बात भी अंश ढूँढ लेती है, और पॉलिसी नंबर या नाम भी।",
    "search.failed": "खोज विफल रही।",
    "search.results.one": "{count} परिणाम",
    "search.results.other": "{count} परिणाम",
    "search.took": "· {ms}ms में मिला",
    "search.page": "पेज {page}",
    "search.match": "{percent}% मेल",
    "search.view": "देखें",
    "search.viewLabel": "{filename} को संदर्भ में देखें",
    "search.chunk": "चंक #{index}",
    "search.foundBy": "{how} से मिला",
    "search.foundByTitle": "हाइब्रिड खोज के किस हिस्से ने यह अंश ढूँढा",
    "search.tokens": "{count} टोकन",
    "search.match.both": "अर्थ + शब्द",
    "search.match.vector": "अर्थ",
    "search.match.keyword": "सटीक शब्द",
    "search.empty.title": "कुछ भी पर्याप्त रूप से मेल नहीं खाया",
    "search.empty.body":
      "हर अंश समानता-सीमा से नीचे रहा, इसलिए कमज़ोर मेल को उत्तर की तरह दिखाने के बजाय कुछ नहीं दिखाया गया। सवाल दूसरे शब्दों में पूछें, या जाँचें कि इस प्रोजेक्ट के दस्तावेज़ों की प्रोसेसिंग पूरी हो गई है।",

    "filter.all": "सभी दस्तावेज़",
    "filter.many": "{count} दस्तावेज़",
    "filter.title": "चुने हुए दस्तावेज़ों तक सीमित करें",
    "filter.listLabel": "खोजने के लिए दस्तावेज़",
  },
};

export default messages;
