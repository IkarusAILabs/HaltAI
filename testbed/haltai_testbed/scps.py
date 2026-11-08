"""Service control policies for the dedicated variant (roadmap 7.1, canary design section 6).

Plain dictionaries, so the main test suite can check them without Node.js.
"""

from __future__ import annotations

from typing import Any

from haltai_testbed.config import BedConfig

CANARY_PULL = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
               "ecr:BatchCheckLayerAvailability"]

# CloudFormation deploys and destroys the testbed through the CDK bootstrap roles, so they
# are exempt from the standing protection SCP. Claim 3 must show no agent can use them.
CDK_ROLES = "arn:aws:iam::*:role/cdk-*"


def _doc(*statements: dict[str, Any]) -> dict[str, Any]:
    return {"Version": "2012-10-17", "Statement": list(statements)}


def halt_agents_scp(cfg: BedConfig) -> dict[str, Any]:
    """Staged, not attached. Denies everything to the declared agent roles."""
    return _doc({"Sid": "HaltAgents", "Effect": "Deny", "Action": "*", "Resource": "*",
                 "Condition": {"ArnLike": {"aws:PrincipalArn": cfg.agent_roles_pattern}}})


def isolate_scp(cfg: BedConfig) -> dict[str, Any]:
    """Staged, not attached. Denies every principal except the execution role.

    Pulls of the canary image stay allowed, by action and resource rather than by principal,
    so the execution role remains the only exempt principal.
    """
    not_exec = {"ArnNotLike": {"aws:PrincipalArn": cfg.execution_role_arn}}
    repo = f"arn:aws:ecr:{cfg.region}:{cfg.agent_account}:repository/haltai/canary"
    return _doc(
        {"Sid": "IsolateAllButHaltAI", "Effect": "Deny",
         "NotAction": ["ecr:GetAuthorizationToken", *CANARY_PULL], "Resource": "*",
         "Condition": not_exec},
        {"Sid": "PullOnlyTheCanary", "Effect": "Deny", "Action": CANARY_PULL,
         "NotResource": repo, "Condition": not_exec},
    )


def protection_scp(cfg: BedConfig) -> dict[str, Any]:
    """Attached at all times. Protects the execution role and HaltAI-staged resources."""
    exempt = {"ArnNotLike": {"aws:PrincipalArn": [cfg.execution_role_arn, CDK_ROLES]}}
    return _doc(
        {"Sid": "ProtectHaltAIRoles", "Effect": "Deny",
         "Action": ["iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePolicy",
                    "iam:DeleteRolePolicy", "iam:UpdateAssumeRolePolicy", "iam:UpdateRole",
                    "iam:PutRolePermissionsBoundary", "iam:DeleteRolePermissionsBoundary",
                    "iam:DeleteRole", "iam:TagRole", "iam:UntagRole", "iam:PassRole",
                    "sts:AssumeRole"],
         "Resource": "arn:aws:iam::*:role/haltai/*", "Condition": exempt},
        {"Sid": "ProtectStagedResources", "Effect": "Deny",
         "Action": ["ec2:CreateNetworkAclEntry", "ec2:ReplaceNetworkAclEntry",
                    "ec2:DeleteNetworkAclEntry", "ec2:DeleteNetworkAcl",
                    "ec2:ReplaceNetworkAclAssociation", "ec2:AuthorizeSecurityGroupEgress",
                    "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress",
                    "ec2:RevokeSecurityGroupIngress", "ec2:DeleteSecurityGroup",
                    "ec2:ModifyVpcEndpoint", "ec2:DeleteVpcEndpoints",
                    "sqs:SetQueueAttributes", "sqs:AddPermission", "sqs:RemovePermission",
                    "sqs:DeleteQueue", "ecr:PutImage", "ecr:BatchDeleteImage",
                    "ecr:SetRepositoryPolicy", "ecr:DeleteRepository",
                    "ecs:DeregisterTaskDefinition", "ecs:DeleteCluster",
                    "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration",
                    "lambda:DeleteFunction"],
         "Resource": "*",
         "Condition": {**exempt, "StringEquals": {"aws:ResourceTag/haltai:managed": "true"}}},
        {"Sid": "ProtectManagedTag", "Effect": "Deny",
         "Action": ["ec2:CreateTags", "ec2:DeleteTags", "tag:TagResources",
                    "tag:UntagResources", "iam:TagRole", "iam:UntagRole", "sqs:TagQueue",
                    "sqs:UntagQueue", "ecr:TagResource", "ecr:UntagResource",
                    "ecs:TagResource", "ecs:UntagResource", "lambda:TagResource",
                    "lambda:UntagResource"],
         "Resource": "*",
         "Condition": {**exempt,
                       "ForAnyValue:StringEquals": {"aws:TagKeys": ["haltai:managed"]}}},
        {"Sid": "StayInTheOrganization", "Effect": "Deny",
         "Action": "organizations:LeaveOrganization", "Resource": "*"},
    )


def delegation_policy(cfg: BedConfig) -> dict[str, Any]:
    """Organizations resource policy: the tooling account may attach and detach SCPs."""
    return _doc({
        "Sid": "DelegateHaltAIScps", "Effect": "Allow",
        "Principal": {"AWS": f"arn:aws:iam::{cfg.tooling_account}:root"},
        "Action": ["organizations:AttachPolicy", "organizations:DetachPolicy",
                   "organizations:DescribeOrganization", "organizations:DescribeAccount",
                   "organizations:DescribePolicy", "organizations:DescribeEffectivePolicy",
                   "organizations:ListRoots", "organizations:ListParents",
                   "organizations:ListPolicies", "organizations:ListPoliciesForTarget",
                   "organizations:ListTargetsForPolicy", "organizations:ListAccounts"],
        "Resource": "*",
        "Condition": {"StringLikeIfExists": {
            "organizations:PolicyType": "SERVICE_CONTROL_POLICY"}},
    })
