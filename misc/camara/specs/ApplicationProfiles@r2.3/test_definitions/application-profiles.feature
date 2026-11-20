  @Application_Profiles
Feature: CAMARA Application Profiles API, v0.6.0 - Operations for Application Profiles

  # Input to be provided by the implementation to the tester
  #
  # Implementation indications:
  # * apiRoot: API root of the server URL
  #
  # Testing assets:
  # * An access token including the scopes application-profiles:create, application-profiles:read,
  #   application-profiles:update and application-profiles:delete
  # * An access token issued to a different API client, to test access to profiles it did not create
  #
  # References to OAS spec schemas refer to schemas specified in application-profiles.yaml.
  # The XCorrelator schema is defined in CAMARA_common.yaml (code/common/).

  Background: Common Application Profiles setup
    Given an environment at "apiRoot"
    And the resource "/application-profiles/v0.6"
    And the header "Content-Type" is set to "application/json"
    And the header "Authorization" is set to a valid access token
    And the header "x-correlator" complies with the schema at "#/components/schemas/XCorrelator"

######### Happy Path Scenarios #################################

  @application_profiles_01_create_with_network_quality_thresholds
  Scenario: Create an application profile with network quality thresholds
    Given the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    And the request body property "$.networkQualityThresholds" is set to a valid set of network quality thresholds
    When the request "createApplicationProfile" is sent
    Then the response status code is 201
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/ApplicationProfile"
    And the response property "$.applicationProfileId" is a valid UUID
    And the response property "$.networkQualityThresholds" has the same value as in the request body

  @application_profiles_02_create_with_compute_resources
  Scenario: Create an application profile with compute resource thresholds
    Given the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    And the request body property "$.computeResources" is set to a valid set of compute resource thresholds
    When the request "createApplicationProfile" is sent
    Then the response status code is 201
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/ApplicationProfile"
    And the response property "$.applicationProfileId" is a valid UUID
    And the response property "$.computeResources" has the same value as in the request body

  @application_profiles_03_update_application_profile
  Scenario: Update an application profile successfully
    Given an existing application profile created by operation "createApplicationProfile"
    And the path parameter "applicationProfileId" is set to the identifier of that application profile
    And the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "updateApplicationProfile" is sent
    Then the response status code is 200
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/ApplicationProfile"
    And the response property "$.applicationProfileId" has the same value as the path parameter "applicationProfileId"
    And the response body thresholds have the same values as in the request body

  @application_profiles_04_read_application_profile
  Scenario: Read an application profile successfully
    Given an existing application profile created by operation "createApplicationProfile"
    And the path parameter "applicationProfileId" is set to the identifier of that application profile
    When the request "getApplicationProfile" is sent
    Then the response status code is 200
    And the response header "Content-Type" is "application/json"
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response body complies with the OAS schema at "#/components/schemas/ApplicationProfile"
    And the response property "$.applicationProfileId" has the same value as the path parameter "applicationProfileId"

  @application_profiles_05_delete_application_profile
  Scenario: Delete an application profile successfully
    Given an existing application profile created by operation "createApplicationProfile"
    And the path parameter "applicationProfileId" is set to the identifier of that application profile
    When the request "deleteApplicationProfile" is sent
    Then the response status code is 204
    And the response header "x-correlator" has same value as the request header "x-correlator"

############### Error response scenarios ###########################

  # 400 Error Scenarios

  @application_profiles_06_create_with_invalid_request_body
  Scenario: Create application profile with a request body not compliant with the schema
    Given the request body is included but is not compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "createApplicationProfile" is sent
    Then the response status code is 400
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @application_profiles_07_create_with_no_request_body
  Scenario: Create application profile without a request body
    Given the request body is not included
    When the request "createApplicationProfile" is sent
    Then the response status code is 400
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @application_profiles_08_create_with_empty_request_body
  Scenario: Create application profile with an empty object as request body
    # At least one of networkQualityThresholds or computeResources is required
    Given the request body is set to {}
    When the request "createApplicationProfile" is sent
    Then the response status code is 400
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @application_profiles_09_create_with_empty_threshold_object
  Scenario Outline: Create application profile with an empty threshold object
    # networkQualityThresholds and computeResources require at least one property
    Given the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    And the request body property "<threshold_property>" is set to {}
    When the request "createApplicationProfile" is sent
    Then the response status code is 400
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

    Examples:
      | threshold_property         |
      | $.networkQualityThresholds |
      | $.computeResources         |

  @application_profiles_10_create_with_unknown_property
  Scenario: Create application profile with a property not declared in the schema
    Given the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    And the request body includes an additional property "$.applicationProfileId" set to a valid UUID
    When the request "createApplicationProfile" is sent
    Then the response status code is 400
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @application_profiles_11_update_with_invalid_request_body
  Scenario: Update application profile with a request body not compliant with the schema
    Given an existing application profile created by operation "createApplicationProfile"
    And the path parameter "applicationProfileId" is set to the identifier of that application profile
    And the request body is included but is not compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "updateApplicationProfile" is sent
    Then the response status code is 400
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @application_profiles_12_update_with_identifier_in_request_body
  Scenario: Update application profile with an identifier in the request body
    # The identifier is only accepted as path parameter; in the body it is an unknown property
    Given an existing application profile created by operation "createApplicationProfile"
    And the path parameter "applicationProfileId" is set to the identifier of that application profile
    And the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    And the request body includes an additional property "$.applicationProfileId" set to the same identifier
    When the request "updateApplicationProfile" is sent
    Then the response status code is 400
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  @application_profiles_13_invalid_path_parameter
  Scenario Outline: Request with an applicationProfileId that is not a valid UUID
    Given the path parameter "applicationProfileId" is set to a value that is not a valid UUID
    And the request body, if required by the operation, is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "<operationId>" is sent
    Then the response status code is 400
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

    Examples:
      | operationId              |
      | getApplicationProfile   |
      | updateApplicationProfile |
      | deleteApplicationProfile |

  @application_profiles_14_invalid_x_correlator
  Scenario: Invalid x-correlator header
    Given the header "x-correlator" does not comply with the schema at "#/components/schemas/XCorrelator"
    And the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "createApplicationProfile" is sent
    Then the response status code is 400
    And the response property "$.status" is 400
    And the response property "$.code" is "INVALID_ARGUMENT"
    And the response property "$.message" contains a user friendly text

  # 401 Error Scenarios

  @application_profiles_15_no_authorization_header
  Scenario: Error response for no header "Authorization"
    Given the header "Authorization" is not sent
    And the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "createApplicationProfile" is sent
    Then the response status code is 401
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 401
    And the response property "$.code" is "UNAUTHENTICATED"
    And the response property "$.message" contains a user friendly text

  @application_profiles_16_expired_access_token
  Scenario: Error response for expired access token
    Given the header "Authorization" is set to an expired access token
    And the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "createApplicationProfile" is sent
    Then the response status code is 401
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 401
    And the response property "$.code" is "UNAUTHENTICATED"
    And the response property "$.message" contains a user friendly text

  @application_profiles_17_invalid_access_token
  Scenario: Error response for invalid access token
    Given the header "Authorization" is set to an invalid access token
    And the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "createApplicationProfile" is sent
    Then the response status code is 401
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 401
    And the response property "$.code" is "UNAUTHENTICATED"
    And the response property "$.message" contains a user friendly text

  # 403 Error Scenarios

  @application_profiles_18_missing_access_token_scope
  Scenario: Missing access token scope
    Given the header "Authorization" is set to an access token that does not include scope "application-profiles:create"
    And the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "createApplicationProfile" is sent
    Then the response status code is 403
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 403
    And the response property "$.code" is "PERMISSION_DENIED"
    And the response property "$.message" contains a user friendly text

  @application_profiles_19_profile_not_owned_by_api_client
  Scenario Outline: Application profile not created by the API client given in the access token
    # To test this, a token has to be obtained for a different API client
    Given an existing application profile created by operation "createApplicationProfile"
    And the path parameter "applicationProfileId" is set to the identifier of that application profile
    And the header "Authorization" is set to a valid access token emitted to an API client which did not create the application profile
    And the request body, if required by the operation, is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "<operationId>" is sent
    Then the response status code is 403
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 403
    And the response property "$.code" is "PERMISSION_DENIED"
    And the response property "$.message" contains a user friendly text

    Examples:
      | operationId              |
      | getApplicationProfile   |
      | updateApplicationProfile |
      | deleteApplicationProfile |

  # 404 Error Scenarios

  @application_profiles_20_read_unknown_profile_id
  Scenario: Read application profile with unknown identifier
    Given the path parameter "applicationProfileId" is set to a random UUID
    When the request "getApplicationProfile" is sent
    Then the response status code is 404
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 404
    And the response property "$.code" is "NOT_FOUND"
    And the response property "$.message" contains a user friendly text

  @application_profiles_21_update_unknown_profile_id
  Scenario: Update application profile with unknown identifier
    Given the path parameter "applicationProfileId" is set to a random UUID
    And the request body is compliant with the schema at "#/components/schemas/ApplicationProfileRequest"
    When the request "updateApplicationProfile" is sent
    Then the response status code is 404
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 404
    And the response property "$.code" is "NOT_FOUND"
    And the response property "$.message" contains a user friendly text

  @application_profiles_22_delete_unknown_profile_id
  Scenario: Delete application profile with unknown identifier
    Given the path parameter "applicationProfileId" is set to a random UUID
    When the request "deleteApplicationProfile" is sent
    Then the response status code is 404
    And the response header "x-correlator" has same value as the request header "x-correlator"
    And the response header "Content-Type" is "application/json"
    And the response property "$.status" is 404
    And the response property "$.code" is "NOT_FOUND"
    And the response property "$.message" contains a user friendly text
