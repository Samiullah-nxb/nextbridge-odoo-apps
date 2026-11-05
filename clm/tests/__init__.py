# Part of NextContract. See LICENSE file for full copyright and licensing details.

# Base
from . import test_cloud_consent
from . import test_clm_contract
from . import test_contract_record_rules
from . import test_module_installation
from . import test_semantic_record_rules
from . import test_contract_field_definition
from . import test_contract_field_value
from . import test_contract_field_security

# Documents
from . import test_contract_version

# Library
from . import test_library
from . import test_docx_tag_mapper
from . import test_base_docx_preview

# Docx Editor
from . import test_docx_editor

# Draft
from . import test_draft_assembly
from . import test_phase4_generation
from . import test_contract_text_parser
from . import test_contract_editing
from . import test_base_docx_versioning
from . import test_base_docx_rendering_consistency
from . import test_base_docx_formatting_fidelity
from . import test_base_docx_header_footer
from . import test_default_format_rendering_consistency
from . import test_signed_docx_no_duplicate_signatures

# Intake
from . import test_request
from . import test_conversion

# Workflow
from . import test_workflow
from . import test_workflow_actions
from . import test_workflow_engine
from . import test_workflow_transitions

# Negotiation
from . import test_negotiation
from . import test_portal_upload
from . import test_portal_security
from . import test_version_comparison
from . import test_pdf_compare_highlight

# Execution
from . import test_execution
from . import test_final_contract
from . import test_amendment

# Lifecycle
from . import test_lifecycle_overview
from . import test_versioned_lifecycle

# Reporting
from . import test_excel_export

# Suite
from . import test_suite_install
from . import test_qa_lifecycle_security
from . import test_qa_workflow_e2e
from . import test_qa_portal_http
from . import test_qa_post_execution
