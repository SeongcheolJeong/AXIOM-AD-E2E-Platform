import { test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { caseStackWorkflow } from "../workflows/case-stack-workflow";

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"../..");
test("case → full original ego/LiDAR sequence → changed stack → same-case comparison",async({page},info)=>{
  await caseStackWorkflow(page,info,root,process.env.AXIOM_TEST_PYTHON ?? path.join(root,".venv/bin/python"),"http://127.0.0.1:8010");
});
