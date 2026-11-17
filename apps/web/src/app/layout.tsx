import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AskData",
  description: "Ask questions about a CSV. Your file never leaves the browser.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
