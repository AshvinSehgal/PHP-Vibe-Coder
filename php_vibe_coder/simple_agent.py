import re
import uuid
import shutil
import json
from pathlib import Path
from .runner import CodeIgniterRunner

class SimplePHPAgent:
    def __init__(self, root, llm, vector_store=None):
        self.root = Path(root)
        self.llm = llm
        self.vector_store = vector_store
        self.knowledge_dir = self.root / "knowledge"
        self.template_dir = self.root / "templates" / "codeigniter-base"
        self.runner = CodeIgniterRunner()

    def build(self, prompt):
        knowledge = self.retrieve(prompt)
        plan = self.create_plan(prompt, knowledge)
        plan = self.prepare_plan(plan, prompt)
        self.validate_plan(plan)
        workspace = self.create_workspace()
        files = self.generate_code(prompt, plan, knowledge)
        planned_files = set(plan["files"])
        generated_files = set(files)
        missing_files = planned_files - generated_files
        extra_files = generated_files - planned_files
        if missing_files:
            raise ValueError("The LLM did not generate: " + ", ".join(sorted(missing_files)))
        if extra_files:
            raise ValueError("The LLM generated unplanned files: " + ", ".join(sorted(extra_files)))
        self.write_files(workspace, files)
        attempts = []
        errors = self.check_generated_code(workspace, plan)
        if not errors:
            errors = self.runner.check(workspace, plan["files"])
        for attempt in range(1, 2):
            if not errors:
                break
            environment_errors = [error for error in errors if error.get("kind") == "environment"]
            if environment_errors:
                break
            corrected_files = self.correct_code(prompt, plan, knowledge, workspace, errors)
            if not corrected_files:
                break
            self.write_files(workspace, corrected_files)
            attempts.append({
                "number": attempt,
                "errors": errors,
                "changed_files": list(
                    corrected_files
                ),
            })
            errors = self.check_generated_code(workspace, plan)
            if not errors:
                errors = self.runner.check(workspace, plan["files"])
        if errors and not any(error.get("kind") == "environment" for error in errors):
            fallback_files = self.basic_fallback_files(plan)
            self.write_files(workspace, fallback_files)
            attempts.append({
                "number": "basic scaffold",
                "errors": errors,
                "changed_files": list(fallback_files),
            })
            errors = self.check_generated_code(workspace, plan)
            if not errors:
                errors = self.runner.check(workspace, plan["files"])
        if not errors:
            status = "working"
        elif any(error.get("kind") == "environment" for error in errors):
            status = "environment_error"
        else:
            status = "could_not_fix"
        return {
            "summary": plan.get("summary", prompt),
            "features": plan.get("features", []),
            "plan": plan,
            "knowledge": knowledge,
            "files": self.read_generated_files(workspace, plan),
            "workspace": str(workspace),
            "status": status,
            "errors": errors,
            "attempts": attempts,
        }

    def retrieve(self, prompt):
        if self.vector_store is not None:
            try:
                return self.vector_store.search(prompt, limit=5)
            except Exception as e:
                print(f"Vector search unavailable: {e}")
        query_words = self.words(prompt)
        matches = []
        for path in self.knowledge_dir.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            score = len(query_words & self.words(text))
            if score:
                matches.append({
                    "source": str(path.relative_to(self.knowledge_dir)),
                    "text": text,
                    "score": score,
                })
        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:3]

    def words(self, text):
        return set(re.findall(r"[a-zA-Z][a-zA-Z0-9]+", text.lower()))
    
    def knowledge_text(self, knowledge):
        sections = []
        for item in knowledge:
            text = item["text"][:2200]
            sections.append(f"SOURCE: {item['source']}\n{text}")
        return "\n\n".join(sections)
    
    def create_plan(self, prompt, knowledge):
        system_prompt = """
        You are a beginner-friendly PHP and CodeIgniter planner.

        Return only valid JSON.

        Use this structure:
        {
        "summary": "short explanation",
        "features": ["feature"],
        "entities": [
            {
            "name": "Entity",
            "fields": ["field"]
            }
        ],
        "files": ["relative/file/path"]
        }

        Rules:
        - Use CodeIgniter 4.
        - Keep the project small.
        - Plan no more than 10 application files.
        - Use controllers, models, views, routes and migrations.
        - Do not put the primary key in an entity's fields list; it is added automatically.
        - Use exact CodeIgniter directory capitalization such as app/Controllers, app/Models, app/Views and app/Database/Migrations.
        - Do not include vendor files.
        - Do not include framework source files.
        - Do not include automated test files.
        """

        user_prompt = f"""
        USER REQUEST:

        {prompt}

        RELEVANT KNOWLEDGE:

        {self.knowledge_text(knowledge)}
        """
        return self.llm.generate_json(system_prompt, user_prompt)

    def prepare_plan(self, plan, prompt=""):
        if not isinstance(plan, dict) or not isinstance(plan.get("files"), list):
            return plan
        replacements = {
            "app/Controller/": "app/Controllers/",
            "app/Model/": "app/Models/",
            "app/View/": "app/Views/",
        }
        files = []
        for filename in plan["files"]:
            if not isinstance(filename, str):
                files.append(filename)
                continue
            for old, new in replacements.items():
                if filename.startswith(old):
                    filename = new + filename[len(old):]
            if filename.startswith("app/Database/Migrations/"):
                name = Path(filename).name
                if not re.match(r"\d{4}-\d{2}-\d{2}-\d{6}_", name):
                    filename = "app/Database/Migrations/2026-01-01-000001_" + name
            files.append(filename)
        needs_routes = any(
            filename.startswith(("app/Controllers/", "app/Views/"))
            for filename in files if isinstance(filename, str)
        )
        if needs_routes and "app/Config/Routes.php" not in files and len(files) < 10:
            files.append("app/Config/Routes.php")
        needs_view = any(
            filename.startswith("app/Controllers/")
            for filename in files if isinstance(filename, str)
        )
        has_view = any(
            filename.startswith("app/Views/")
            for filename in files if isinstance(filename, str)
        )
        if needs_view and not has_view and len(files) < 10:
            files.append("app/Views/index.php")
        database_requested = re.search(
            r"\b(database|mysql|table|stored?|save|crud)\b",
            prompt,
            flags=re.IGNORECASE,
        )
        if database_requested:
            entity = re.sub(r"[^a-zA-Z0-9]", "", self.primary_entity(plan)) or "Item"
            database_files = []
            if not any(filename.startswith("app/Models/") for filename in files):
                database_files.append(f"app/Models/{entity}Model.php")
            if not any(filename.startswith("app/Database/Migrations/") for filename in files):
                database_files.append(
                    "app/Database/Migrations/"
                    f"2026-01-01-000001_Create{entity}s.php"
                )
            if len(files) + len(database_files) > 10:
                raise ValueError("The database plan needs a model and migration but exceeds 10 files")
            files.extend(database_files)
        plan["files"] = list(dict.fromkeys(files))
        return plan
    
    def validate_plan(self, plan):
        if not isinstance(plan, dict):
            raise ValueError("The LLM plan must be a JSON object")
        files = plan.get("files")
        if not isinstance(files, list):
            raise ValueError("The plan must contain a files list")
        if not files:
            raise ValueError("The LLM returned an empty file plan")
        if len(files) > 10:
            raise ValueError("The LLM planned more than 10 files")
        if len(files) != len(set(files)):
            raise ValueError("The LLM planned duplicate files")
        for filename in files:
            self.validate_filename(filename)
    
    def generate_code(self, prompt, plan, knowledge):
        system_prompt = """
        Generate one small, complete CodeIgniter 4 file.
        Return code only, with no Markdown fence or explanation.
        Never use Laravel or Illuminate. Never use PHP type declarations.
        Do not add authentication, admin pages or unrequested features.
        Keep the file under 50 lines and finish every class, method and HTML tag.
        """
        files = {}
        entity = self.primary_entity(plan)
        for filename in plan["files"]:
            user_prompt = f"""
            Generate only this file: {filename}
            User request: {prompt}
            Main entity: {entity}
            {self.file_guidance(filename, entity)}
            """
            answer = self.llm.generate(
                system_prompt,
                user_prompt,
                max_new_tokens=self.file_token_limit(filename),
            )
            files[filename] = self.clean_file_content(answer, filename)
        return files

    def primary_entity(self, plan):
        entities = plan.get("entities", [])
        if entities and isinstance(entities[0], dict):
            name = entities[0].get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        return "Item"

    def file_guidance(self, filename, entity):
        if filename == "app/Config/Routes.php":
            return f"Begin with <?php and define only $routes->get('/', '{entity}Controller::index');"
        if filename.startswith("app/Controllers/"):
            return (
                f"Use namespace App\\Controllers. Extend BaseController. "
                f"In index(), create App\\Models\\{entity}Model, call findAll(), "
                "and return view('index', ['items' => $items])."
            )
        if filename.startswith("app/Models/"):
            return (
                "Use namespace App\\Models, import CodeIgniter\\Model and extend Model. "
                "Set table, primaryKey, allowedFields and returnType. Add no methods."
            )
        if filename.startswith("app/Database/Migrations/"):
            return (
                "Use namespace App\\Database\\Migrations, import "
                "CodeIgniter\\Database\\Migration and extend Migration. "
                "Implement complete up() and down() methods using $this->forge."
            )
        if filename.startswith("app/Views/"):
            return (
                "Output an HTML page, not a class or controller. Loop over $items, "
                "display the requested fields, and escape values with esc()."
            )
        return "Generate the complete requested file using plain beginner-friendly PHP."

    def file_token_limit(self, filename):
        if filename == "app/Config/Routes.php":
            return 100
        if filename.startswith("app/Views/"):
            return 260
        if filename.startswith("app/Database/Migrations/"):
            return 420
        return 280

    def entity_fields(self, plan):
        entities = plan.get("entities", [])
        if entities and isinstance(entities[0], dict):
            fields = entities[0].get("fields", [])
            cleaned = []
            for field in fields:
                if not isinstance(field, str):
                    continue
                name = re.sub(r"[^a-zA-Z0-9_]", "_", field.split(":", 1)[0]).strip("_").lower()
                if name and name != "id" and name not in cleaned:
                    cleaned.append(name)
            if cleaned:
                return cleaned[:8]
        return ["name"]

    def basic_fallback_files(self, plan):
        entity = re.sub(r"[^a-zA-Z0-9]", "", self.primary_entity(plan)) or "Item"
        fields = self.entity_fields(plan)
        table = entity.lower() + "s"
        files = {}
        for filename in plan["files"]:
            if filename == "app/Config/Routes.php":
                files[filename] = (
                    "<?php\n\n"
                    f"$routes->get('/', '{entity}Controller::index');\n"
                )
            elif filename.startswith("app/Controllers/"):
                files[filename] = (
                    "<?php\n\n"
                    "namespace App\\Controllers;\n\n"
                    f"use App\\Models\\{entity}Model;\n\n"
                    f"class {entity}Controller extends BaseController\n"
                    "{\n"
                    "    public function index()\n"
                    "    {\n"
                    f"        $model = new {entity}Model();\n"
                    "        return view('index', ['items' => $model->findAll()]);\n"
                    "    }\n"
                    "}\n"
                )
            elif filename.startswith("app/Models/"):
                allowed = ", ".join(f"'{field}'" for field in fields)
                files[filename] = (
                    "<?php\n\n"
                    "namespace App\\Models;\n\n"
                    "use CodeIgniter\\Model;\n\n"
                    f"class {entity}Model extends Model\n"
                    "{\n"
                    f"    protected $table = '{table}';\n"
                    "    protected $primaryKey = 'id';\n"
                    f"    protected $allowedFields = [{allowed}];\n"
                    "    protected $returnType = 'array';\n"
                    "}\n"
                )
            elif filename.startswith("app/Database/Migrations/"):
                files[filename] = self.basic_migration(filename, table, fields)
            elif filename.startswith("app/Views/"):
                files[filename] = self.basic_view(entity, fields)
        return files

    def basic_migration(self, filename, table, fields):
        class_name = Path(filename).stem.split("_", 1)[-1]
        definitions = []
        for field in fields:
            if field in ("price", "amount", "cost"):
                definition = "['type' => 'DECIMAL', 'constraint' => '10,2', 'default' => 0]"
            elif field in ("quantity", "count", "stock", "age") or field.endswith("_id"):
                definition = "['type' => 'INT', 'constraint' => 11, 'default' => 0]"
            else:
                definition = "['type' => 'VARCHAR', 'constraint' => 255]"
            definitions.append(f"            '{field}' => {definition},")
        field_text = "\n".join(definitions)
        return (
            "<?php\n\n"
            "namespace App\\Database\\Migrations;\n\n"
            "use CodeIgniter\\Database\\Migration;\n\n"
            f"class {class_name} extends Migration\n"
            "{\n"
            "    public function up()\n"
            "    {\n"
            "        $this->forge->addField([\n"
            "            'id' => ['type' => 'INT', 'constraint' => 11, 'unsigned' => true, 'auto_increment' => true],\n"
            f"{field_text}\n"
            "        ]);\n"
            "        $this->forge->addKey('id', true);\n"
            f"        $this->forge->createTable('{table}', true);\n"
            "    }\n\n"
            "    public function down()\n"
            "    {\n"
            f"        $this->forge->dropTable('{table}', true);\n"
            "    }\n"
            "}\n"
        )

    def basic_view(self, entity, fields):
        headings = "\n".join(f"                <th>{field.replace('_', ' ').title()}</th>" for field in fields)
        cells = "\n".join(f"                <td><?= esc($item['{field}']) ?></td>" for field in fields)
        return (
            "<!doctype html>\n"
            "<html lang=\"en\">\n"
            "<head><meta charset=\"utf-8\"><title>"
            f"{entity} List</title></head>\n"
            "<body>\n"
            f"    <h1>{entity} List</h1>\n"
            "    <table border=\"1\">\n"
            f"        <thead><tr>\n{headings}\n        </tr></thead>\n"
            "        <tbody>\n"
            "        <?php foreach ($items as $item): ?>\n"
            f"            <tr>\n{cells}\n            </tr>\n"
            "        <?php endforeach; ?>\n"
            "        </tbody>\n"
            "    </table>\n"
            "</body>\n"
            "</html>\n"
        )

    def clean_file_content(self, answer, filename):
        if not isinstance(answer, str):
            raise ValueError(f"The LLM returned invalid contents for {filename}")
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
        fenced = re.search(r"```(?:[a-zA-Z0-9_-]+)?\s*\n(.*?)```", answer, flags=re.DOTALL)
        if fenced:
            answer = fenced.group(1).strip()
        answer = re.sub(r"^```(?:[a-zA-Z0-9_-]+)?\s*\n?", "", answer).strip()
        answer = re.sub(r"\n?```$", "", answer).strip()
        if "<<<FILE" in answer:
            answer = re.sub(r"^.*?<<<FILE.*?>>>\s*", "", answer, count=1, flags=re.DOTALL)
            answer = re.split(r"<<<(?:END[_ ]FILE|FILE)", answer, maxsplit=1)[0].strip()
        if filename.endswith(".php") and "/Views/" not in filename:
            php_start = answer.find("<?php")
            if php_start != -1:
                answer = answer[php_start:]
        if not answer.strip():
            raise ValueError(f"The LLM returned an empty file: {filename}")
        return answer.strip() + "\n"
    
    def parse_file_blocks(self, answer):
        if not isinstance(answer, str):
            raise ValueError("The LLM file response must be text")
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL)
        pattern = (
            r"<<<FILE\s*:?\s*(.+?)>>>\s*\n?"
            r"(.*?)"
            r"\n?<<<END[_ ]FILE>>>"
        )
        matches = re.findall(pattern, answer, flags=re.DOTALL)
        if not matches:
            start = answer.find("{")
            end = answer.rfind("}")
            if start != -1 and end > start:
                json_text = answer[start:end + 1]
                json_text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_text)
                try:
                    data = json.loads(json_text)
                    items = data.get("files", [])
                    matches = [
                        (item.get("path", ""), item.get("content", ""))
                        for item in items
                        if isinstance(item, dict)
                    ]
                except json.JSONDecodeError:
                    matches = []
        if not matches:
            preview = answer.strip()[:1500]
            raise ValueError("The LLM did not return recognizable files. Response preview:\n" + preview)
        files = {}
        for filename, contents in matches:
            if not isinstance(filename, str) or not isinstance(contents, str):
                raise ValueError("Every LLM file needs a text path and text contents")
            filename = filename.strip()
            contents = contents.strip()
            self.validate_filename(filename)
            if filename in files:
                raise ValueError(f"The LLM generated a duplicate file: {filename}")
            if not contents:
                raise ValueError(f"The LLM returned an empty file: {filename}")
            files[filename] = contents + "\n"
        return files
    
    def create_workspace(self):
        job_id = uuid.uuid4().hex[:8]
        workspace = self.root / "storage" / "jobs" / job_id / "project"
        shutil.copytree(
            self.template_dir,
            workspace,
            ignore=shutil.ignore_patterns(
                ".git",
                "logs",
                "cache",
            ),
        )
        for folder in ("cache", "logs", "session", "uploads"):
            workspace.joinpath("writable", folder).mkdir(parents=True, exist_ok=True)
        return workspace
    
    def validate_filename(self, filename):
        if not isinstance(filename, str):
            raise ValueError("Every generated path must be text")
        relative = Path(filename)
        if relative.is_absolute():
            raise ValueError("Absolute paths are not allowed")
        if ".." in relative.parts:
            raise ValueError("Parent-directory paths are not allowed")
        allowed_folders = ("app/", "public/", "writable/")
        if not filename.startswith(allowed_folders):
            raise ValueError(f"Generated path is not allowed: {filename}")
    
    def safe_path(self, workspace, filename):
        self.validate_filename(filename)
        return Path(workspace) / filename
    
    def write_files(self, workspace, files):
        for filename, contents in files.items():
            path = self.safe_path(workspace, filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")

    def check_generated_code(self, workspace, plan):
        errors = []
        forbidden = ("Illuminate\\", "Eloquent", "extends Controller")
        for filename, contents in self.read_generated_files(workspace, plan).items():
            problems = [word for word in forbidden if word in contents]
            if filename == "app/Config/Routes.php" and "$routes->" not in contents:
                problems.append("missing $routes-> route declaration")
            if filename.startswith("app/Controllers/") and "extends BaseController" not in contents:
                problems.append("controller must extend BaseController")
            if filename.startswith("app/Models/") and "namespace App\\Models" not in contents:
                problems.append("model has the wrong namespace")
            if filename.startswith("app/Database/Migrations/") and "extends Migration" not in contents:
                problems.append("file is not a CodeIgniter migration")
            if filename.startswith("app/Views/") and "namespace App\\Controllers" in contents:
                problems.append("view contains controller code")
            if re.search(r"function\s+\w+\s*\([^)]*\b(?:array|string|int|float|bool)\s+\$", contents):
                problems.append("PHP parameter type declaration")
            if problems:
                errors.append({
                    "command": ["framework-check", filename],
                    "return_code": 1,
                    "output": (
                        f"{filename} failed the framework check: {', '.join(problems)}."
                    ),
                    "kind": "code",
                })
        return errors
    
    def correct_code(self, prompt, plan, knowledge, workspace,errors):
        current_files = self.read_generated_files(workspace, plan)
        system_prompt = """
        Repair one small, complete CodeIgniter 4 file.
        Return code only, with no Markdown fence or explanation.
        Never use Laravel or Illuminate. Never use PHP type declarations.
        Keep the file under 50 lines and close every class, method and HTML tag.
        """

        error_text = json.dumps(errors)
        targets = [
            filename for filename in plan["files"]
            if filename in error_text or Path(filename).name in error_text
        ]
        if not targets:
            commands = " ".join(
                " ".join(error.get("command", []))
                for error in errors
            )
            if "migrate" in commands:
                targets = [
                    filename for filename in plan["files"]
                    if filename.startswith("app/Database/Migrations/")
                ]
            elif "serve" in commands:
                targets = [
                    filename for filename in plan["files"]
                    if filename == "app/Config/Routes.php"
                    or filename.startswith("app/Controllers/")
                ]
            else:
                targets = plan["files"][:1]
        corrected = {}
        entity = self.primary_entity(plan)
        for filename in targets:
            user_prompt = f"""
            Repair only this file: {filename}
            User request: {prompt}
            Main entity: {entity}
            Required pattern: {self.file_guidance(filename, entity)}
            Error: {json.dumps(errors)[:1800]}
            Current code:
            {current_files.get(filename, "")}
            """
            answer = self.llm.generate(
                system_prompt,
                user_prompt,
                max_new_tokens=self.file_token_limit(filename),
            )
            corrected[filename] = self.clean_file_content(answer, filename)
        return corrected

    def read_generated_files(self, workspace, plan):
        files = {}
        for filename in plan.get("files", []):
            path = self.safe_path(workspace, filename)
            if path.is_file():
                files[filename] = path.read_text(encoding="utf-8")
        return files
