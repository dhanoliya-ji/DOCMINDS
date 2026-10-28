// document (lib/progress.ts), English and Hindi.

import type { AreaMessages } from "./index";

const messages: AreaMessages = {
  en: {
    "progress.readingPages": "Reading page {done} of {total}",
    "progress.readingFile": "Reading the file ({chunks} chunks so far)",
    "progress.embedding": "Embedding {embedded} of {total} chunks",
    "progress.waiting": "Waiting for the embedding rate limit{wait}: {embedded} of {total} chunks done",
    "progress.paused":
      "Paused for the embedding rate limit{wait}: {embedded} of {total} chunks done, resumes automatically",
    "progress.about": " (about {duration})",
    "progress.wait.second": "{n} second",
    "progress.wait.seconds": "{n} seconds",
    "progress.wait.minutes": "{n} minutes",
    "progress.wait.hours": "{n} hours",
    "progress.wait.days": "{n} days",
  },
  hi: {
    "progress.readingPages": "पृष्ठ {done} / {total} पढ़ा जा रहा है",
    "progress.readingFile": "फ़ाइल पढ़ी जा रही है (अब तक {chunks} खंड)",
    "progress.embedding": "{total} में से {embedded} खंड एम्बेड हुए",
    "progress.waiting": "एम्बेडिंग दर-सीमा की प्रतीक्षा{wait}: {total} में से {embedded} खंड पूरे",
    "progress.paused":
      "एम्बेडिंग दर-सीमा के कारण रुका है{wait}: {total} में से {embedded} खंड पूरे, अपने आप फिर शुरू होगा",
    "progress.about": " (लगभग {duration})",
    "progress.wait.second": "{n} सेकंड",
    "progress.wait.seconds": "{n} सेकंड",
    "progress.wait.minutes": "{n} मिनट",
    "progress.wait.hours": "{n} घंटे",
    "progress.wait.days": "{n} दिन",
  },
};

export default messages;
