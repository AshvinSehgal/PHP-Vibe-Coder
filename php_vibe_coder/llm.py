import json
import os
import re

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

class LocalLLM:
    def __init__(self):
        self.model_name = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
        self.model = None
        self.tokenizer = None

    def load(self):
        if self.model is None:
            torch.set_num_threads(min(4, os.cpu_count() or 1))
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                local_files_only=True,
            )
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_name,
                local_files_only=True,
                low_cpu_mem_usage=True,
            )
            self.model.eval()

    def generate(self, system_prompt, user_prompt, max_new_tokens=600):
        self.load()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        new_tokens = output[0][inputs["input_ids"].shape[-1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

    def generate_json(self, system_prompt, user_prompt):
        answer = self.generate(system_prompt, user_prompt, max_new_tokens=300)
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL)
        start = answer.find("{")
        end = answer.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("The LLM did not return JSON")
        return json.loads(answer[start:end + 1])
