import { useParams } from 'react-router-dom';

/** Published app: anyone can open /app/:slug — served directly by the backend runtime. */
export default function PublicApp() {
  const { slug = '' } = useParams();
  return (
    <iframe
      title={slug}
      src={`/api/v1/forge/runtime/${slug}`}
      className="h-screen w-full border-0 bg-white"
    />
  );
}
