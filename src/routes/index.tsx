import { createFileRoute } from "@tanstack/react-router";
import { NimboVoDApp } from "@/components/nimbovod-app";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return <NimboVoDApp />;
}
