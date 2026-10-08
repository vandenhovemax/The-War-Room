import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";
import Header from "@/components/layout/Header";
import ThemeProvider from "@/components/ThemeProvider";
import TaskManagerProvider from "@/contexts/TaskManager";
import { SidebarProvider } from "@/contexts/SidebarContext";
import ToastStack from "@/components/ui/Toast";
import MainContent from "@/components/layout/MainContent";

export const metadata: Metadata = {
  title: "The War Room",
  description: "Systematic trading platform for Bybit USDT perpetuals",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-ag-dark text-ag-text antialiased">
        <ThemeProvider>
          <TaskManagerProvider>
            <SidebarProvider>
              <Sidebar />
              <MainContent>
                <Header />
                <main className="flex-1 p-6">
                  {children}
                </main>
              </MainContent>
            </SidebarProvider>
            <ToastStack />
          </TaskManagerProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
