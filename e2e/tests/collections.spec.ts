import { test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { collectionWorkflow } from "../workflows/collection-workflow";

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"../..");
test("cases + normal data → collection → explicit curation → ordinary dataset evaluation",async({page},info)=>{
  await collectionWorkflow(page,info,root,process.env.AXIOM_TEST_PYTHON ?? path.join(root,".venv/bin/python"),"http://127.0.0.1:8010");
});
