import { initializeApp } from "firebase/app";
import {
  getAuth,
  signInWithPopup,
  GoogleAuthProvider,
  onAuthStateChanged,
  signOut,
  Auth,
  User,
} from "firebase/auth";
import {
  getFirestore,
  Firestore,
  collection,
  getDocs,
  getDoc,
  addDoc,
  setDoc,
  doc,
  updateDoc,
  serverTimestamp,
  query,
  orderBy,
  where,
  arrayUnion,
  onSnapshot,
  Unsubscribe,
} from "firebase/firestore";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

let app: ReturnType<typeof initializeApp> | null = null;
let auth: Auth | null = null;
let db: Firestore | null = null;

export function initializeFirebase() {
  if (!app) {
    app = initializeApp(firebaseConfig);
    auth = getAuth(app);
    db = getFirestore(app);
  }
  return { auth, db };
}

export function getAuthInstance(): Auth {
  if (!auth) initializeFirebase();
  return auth!;
}

export function getDb(): Firestore {
  if (!db) initializeFirebase();
  return db!;
}

export async function signInWithGoogle(): Promise<User> {
  const authInstance = getAuthInstance();
  const provider = new GoogleAuthProvider();
  const result = await signInWithPopup(authInstance, provider);
  return result.user;
}

export async function logOut(): Promise<void> {
  const authInstance = getAuthInstance();
  await signOut(authInstance);
}

export function onAuthStateChangeListener(
  callback: (user: User | null) => void
) {
  const authInstance = getAuthInstance();
  return onAuthStateChanged(authInstance, callback);
}

export interface Conversation {
  id: string;
  title: string;
  ownerId: string;
  members: string[]; // emails granted access (read/write)
  createdAt: any;
  updatedAt: any;
}

export interface Attachment {
  type: "image" | "text" | "document";
  data: string; // base64
  filename: string;
  mimeType: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  createdAt?: any;
  authorEmail?: string;
  authorName?: string;
  authorPhoto?: string;
  attachments?: Attachment[];
}

// Conversations now live in a top-level collection so they can be shared
// across users. Access is granted by email via `members`.
export async function loadConversations(userEmail: string): Promise<Conversation[]> {
  const db = getDb();
  const conversationsRef = collection(db, "conversations-v2");
  const q = query(
    conversationsRef,
    where("members", "array-contains", userEmail),
    orderBy("updatedAt", "desc")
  );
  const snapshot = await getDocs(q);
  return snapshot.docs.map((doc) => ({
    id: doc.id,
    ...doc.data(),
  })) as Conversation[];
}

export async function loadMessages(conversationId: string): Promise<Message[]> {
  const db = getDb();
  const docRef = doc(db, `conversations-v2/${conversationId}`);
  const docSnap = await getDoc(docRef);
  return (docSnap.data()?.messages || []) as Message[];
}

export async function saveMessage(
  ownerId: string,
  ownerEmail: string,
  ownerName: string | null,
  ownerPhoto: string | null,
  conversationId: string,
  message: Message,
  title?: string
) {
  const db = getDb();
  const conversationRef = doc(db, `conversations-v2/${conversationId}`);

  // Clean message to remove undefined fields (Firestore doesn't allow undefined)
  const cleanMessage: any = {
    role: message.role,
    content: message.content,
    createdAt: message.createdAt || new Date(),
  };

  if (message.authorEmail) cleanMessage.authorEmail = message.authorEmail;
  if (message.authorName) cleanMessage.authorName = message.authorName;
  if (message.authorPhoto) cleanMessage.authorPhoto = message.authorPhoto;
  if (message.attachments && message.attachments.length > 0) {
    cleanMessage.attachments = message.attachments.map((att: any) => ({
      type: att.type,
      data: att.data,
      filename: att.filename,
      mimeType: att.mimeType,
      ...(att.textContent && { textContent: att.textContent }),
    }));
  }

  const docSnap = await getDoc(conversationRef);

  if (!docSnap.exists()) {
    await setDoc(conversationRef, {
      title: title || "New Conversation",
      ownerId,
      members: [ownerEmail],
      messages: [cleanMessage],
      createdAt: serverTimestamp(),
      updatedAt: serverTimestamp(),
    });
  } else {
    await updateDoc(conversationRef, {
      messages: [
        ...(docSnap.data()?.messages || []),
        cleanMessage,
      ],
      updatedAt: serverTimestamp(),
    });
  }
}

export async function shareConversation(
  conversationId: string,
  email: string
): Promise<void> {
  const db = getDb();
  const conversationRef = doc(db, `conversations-v2/${conversationId}`);
  await updateDoc(conversationRef, {
    members: arrayUnion(email.trim().toLowerCase()),
  });
}

export function subscribeToMessages(
  conversationId: string,
  callback: (messages: Message[]) => void
): Unsubscribe {
  const db = getDb();
  const docRef = doc(db, `conversations-v2/${conversationId}`);

  return onSnapshot(docRef, (docSnap) => {
    if (docSnap.exists()) {
      const messages = (docSnap.data()?.messages || []) as Message[];
      callback(messages);
    }
  });
}
