/**
 * Return an Axios promise's response body.
 *
 * Almost every `api/*` function is a passthrough whose whole job is to drop the
 * Axios envelope. Spelling that as `const res = await ...; return res.data;` is
 * two lines of ceremony around one idea, ~175 times over.
 *
 * Deliberately NOT exported from `utils/apiClient`: every `api/*` test mocks
 * that module wholesale, and a helper living there would be mocked away with
 * it, forcing each of those mocks to re-implement it. Here it stays real in the
 * tests, which is what makes the collapse behaviour-preserving rather than
 * behaviour-per-mock.
 *
 * @param {Promise<{data: any}>} p - an Axios request promise.
 * @returns {Promise<any>} the response body.
 */
export const unwrap = (p) => p.then((res) => res.data);
