import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { ToastProvider } from "./components/Toasts";
import Landing from "./pages/Landing";
import CreatePitch from "./pages/CreatePitch";
import Generating from "./pages/Generating";
import Review from "./pages/Review";
import Complete from "./pages/Complete";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Landing />} />
          <Route path="new" element={<CreatePitch />} />
          <Route path="generating/:jobId" element={<Generating />} />
          <Route path="runs/:runId" element={<Review />} />
          <Route path="runs/:runId/complete" element={<Complete />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </ToastProvider>
  );
}
