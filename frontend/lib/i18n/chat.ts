/**
 * i18n/chat.ts -- the chat panel's strings, English and Hindi.
 * See i18n/index.ts. Keys are prefixed "chat." so areas never collide.
 */

import type { AreaMessages } from "./index";

const messages: AreaMessages = {
  en: {
    "chat.suggestion.1": "Summarise the key points of these documents",
    "chat.suggestion.2": "What are the main policies described here?",
    "chat.suggestion.3": "List any deadlines or dates mentioned",
    "chat.mode.rag": "Quick answer",
    "chat.mode.rag.hint": "One search, one answer. Fastest; best for simple, direct questions.",
    "chat.mode.agent": "Deep research",
    "chat.mode.agent.hint": "Searches several times and combines documents. Slower; best for complex questions.",
    "chat.modeGroup": "Answer mode",
    "chat.newConversation": "New conversation",
    "chat.history": "History",
    "chat.export": "Export",
    "chat.exportTitle": "Download this conversation as Markdown",
    "chat.emptyTitle": "Ask your documents",
    "chat.emptyBody":
      "Questions are answered only from the documents in this project, and every answer shows the page it came from.",
    "chat.placeholder": "Ask a question about these documents...",
    "chat.stop": "Stop",
    "chat.stopLabel": "Stop answering",
    "chat.send": "Send",
    "chat.researchSteps": "Research steps",
    "chat.researching": "Researching your documents...",
    "chat.phase.search": "Searching your documents",
    "chat.phase.read": "Reading the relevant sections",
    "chat.phase.summarise": "Writing the summary",
  },
  hi: {
    "chat.suggestion.1": "इन दस्तावेज़ों के मुख्य बिंदुओं का सारांश दें",
    "chat.suggestion.2": "यहाँ वर्णित मुख्य नीतियाँ क्या हैं?",
    "chat.suggestion.3": "उल्लिखित सभी समय-सीमाएँ या तारीख़ें बताएँ",
    "chat.mode.rag": "त्वरित उत्तर",
    "chat.mode.rag.hint": "एक खोज, एक उत्तर। सबसे तेज़; सरल, सीधे प्रश्नों के लिए सर्वोत्तम।",
    "chat.mode.agent": "गहन शोध",
    "chat.mode.agent.hint": "कई बार खोजता है और दस्तावेज़ों को जोड़ता है। धीमा; जटिल प्रश्नों के लिए सर्वोत्तम।",
    "chat.modeGroup": "उत्तर का तरीका",
    "chat.newConversation": "नई बातचीत",
    "chat.history": "इतिहास",
    "chat.export": "निर्यात",
    "chat.exportTitle": "इस बातचीत को Markdown के रूप में डाउनलोड करें",
    "chat.emptyTitle": "अपने दस्तावेज़ों से पूछें",
    "chat.emptyBody":
      "प्रश्नों के उत्तर केवल इस प्रोजेक्ट के दस्तावेज़ों से दिए जाते हैं, और हर उत्तर वह पृष्ठ दिखाता है जहाँ से वह आया।",
    "chat.placeholder": "इन दस्तावेज़ों के बारे में प्रश्न पूछें...",
    "chat.stop": "रोकें",
    "chat.stopLabel": "उत्तर देना रोकें",
    "chat.send": "भेजें",
    "chat.researchSteps": "शोध के चरण",
    "chat.researching": "आपके दस्तावेज़ों पर शोध हो रहा है...",
    "chat.phase.search": "आपके दस्तावेज़ खोजे जा रहे हैं",
    "chat.phase.read": "संबंधित अनुभाग पढ़े जा रहे हैं",
    "chat.phase.summarise": "सारांश लिखा जा रहा है",
  },
};

export default messages;
