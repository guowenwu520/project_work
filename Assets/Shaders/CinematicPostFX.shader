Shader "Hidden/ChangeBlindness/CinematicPostFX"
{
    Properties
    {
        _MainTex ("Texture", 2D) = "white" {}
        _Exposure ("Exposure", Float) = 1.02
        _Contrast ("Contrast", Float) = 1.06
        _Saturation ("Saturation", Float) = 0.97
        _Vignette ("Vignette", Float) = 0.18
        _Grain ("Grain", Float) = 0.012
        _TimeSeed ("Time Seed", Float) = 0
    }

    SubShader
    {
        Cull Off ZWrite Off ZTest Always
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float _Exposure;
            float _Contrast;
            float _Saturation;
            float _Vignette;
            float _Grain;
            float _TimeSeed;

            float rand(float2 co)
            {
                return frac(sin(dot(co.xy, float2(12.9898, 78.233)) + _TimeSeed) * 43758.5453);
            }

            fixed4 frag(v2f_img input) : SV_Target
            {
                float2 uv = input.uv;
                float3 color = tex2D(_MainTex, uv).rgb;
                color *= _Exposure;

                color = color / (1.0 + color);
                color = pow(max(color, 0.0), 1.0 / 1.03);

                float luminance = dot(color, float3(0.2126, 0.7152, 0.0722));
                color = lerp(luminance.xxx, color, _Saturation);
                color = (color - 0.5) * _Contrast + 0.5;

                float2 centered = uv * 2.0 - 1.0;
                float vignette = smoothstep(1.25, 0.24, dot(centered, centered));
                color *= lerp(1.0 - _Vignette, 1.0, vignette);

                float noise = rand(uv * _ScreenParams.xy) - 0.5;
                color += noise * _Grain;
                return fixed4(saturate(color), 1.0);
            }
            ENDCG
        }
    }
}
