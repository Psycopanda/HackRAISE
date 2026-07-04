import { OpenAI } from "openai";
import { saveMessage } from "@/app/lib/firebase";

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

export async function POST(req: Request) {
  const { messages, userId, userEmail, userName, userPhoto, conversationId } = await req.json();

  // Transform messages to include image/document content if attachments exist
  const transformedMessages = messages.map((msg: any) => {
    if (msg.attachments && msg.attachments.length > 0) {
      const content: any[] = [{ type: "text", text: msg.content }];

      msg.attachments.forEach((att: any) => {
        if (att.type === "image") {
          // Images: use vision API
          content.push({
            type: "image_url",
            image_url: {
              url: `data:${att.mimeType};base64,${att.data}`,
            },
          });
        } else if (att.type === "document") {
          // Send document text if extracted
          if (att.textContent) {
            content.push({
              type: "text",
              text: `\n[Document: ${att.filename}]\n${att.textContent}`,
            });
          }
        }
      });

      return { ...msg, content };
    }
    return msg;
  });

  const stream = await openai.chat.completions.create({
    model: "gpt-5-nano",
    stream: true,
    messages: transformedMessages,
  });

  const readableStream = new ReadableStream({
    async start(controller) {
      let fullContent = "";
      for await (const chunk of stream) {
        const delta = chunk.choices[0]?.delta?.content;
        if (delta) {
          fullContent += delta;
          controller.enqueue(new TextEncoder().encode(delta));
        }
      }

      // Save full assistant message to Firestore
      if (userId && userEmail && conversationId && fullContent) {
        await saveMessage(userId, userEmail, userName, userPhoto, conversationId, {
          role: "assistant",
          content: fullContent,
          createdAt: new Date(),
          authorName: "AI",
          authorEmail: "ai@assistant.local",
        });
      }

      controller.close();
    },
  });

  return new Response(readableStream, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}
